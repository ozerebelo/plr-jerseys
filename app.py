import json
import os
import re
import secrets
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from itertools import groupby
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from flask import Flask, Response, g, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

UPLOAD_FOLDER = Path(__file__).parent / "public" / "uploads"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app = Flask(__name__, static_folder="public", static_url_path="")

STATUSES = ["pending", "confirmed", "paid", "fulfilled", "rejected", "cancelled"]
STATUS_LABELS = {
    "pending": "Pendente",
    "confirmed": "Confirmado",
    "paid": "Pago",
    "fulfilled": "Entregue",
    "rejected": "Rejeitado",
    "cancelled": "Cancelado",
}
SIZES = ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL"]
CATEGORIES = [
    "1ª Liga",
    "La Liga",
    "Ligue 1",
    "Premier League",
    "Bundesliga",
    "Serie A",
    "Internacional",
    "Outros",
    "Vintage",
]
# Display order for the order-form catalog dropdown: Vintage first, leagues in the
# middle, Internacional last. (Custom/"+ Outro artigo" is handled separately, pinned
# above this list in the template.)
CATEGORY_DISPLAY_ORDER = ["Vintage"] + [c for c in CATEGORIES if c not in ("Vintage", "Internacional")] + ["Internacional"]
KIT_TYPES = ["Casa", "Fora", "Alternativa", "Outro"]
_season_start_year = datetime.now().year if datetime.now().month >= 7 else datetime.now().year - 1
SEASONS = [f"{_season_start_year - i}/{_season_start_year - i + 1}" for i in range(4)] + [
    "Mundial 2026",
    "Euro 2024",
    "Outras Seleções",
]
BASE_PRICE = 22.0
VINTAGE_PRICE = 25.0
PERSONALIZATION_PRICE = 2.5
CUSTOM_REQUEST_STATUSES = ["pending", "answered", "rejected"]
CUSTOM_REQUEST_STATUS_LABELS = {
    "pending": "Pendente",
    "answered": "Respondido",
    "rejected": "Rejeitado",
}


def check_admin_auth(username, password):
    expected_user = os.environ.get("ADMIN_USERNAME", "admin")
    expected_pass = os.environ.get("ADMIN_PASSWORD")
    if not expected_pass:
        return False
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(password, expected_pass)


@app.before_request
def require_admin_auth():
    if request.path.startswith("/admin"):
        auth = request.authorization
        if not auth or not check_admin_auth(auth.username, auth.password):
            return Response(
                "Autenticação necessária para aceder à administração.",
                401,
                {"WWW-Authenticate": 'Basic realm="Admin"'},
            )


def get_db():
    if "db" not in g:
        g.db = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with psycopg.connect(os.environ["DATABASE_URL"]) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                coworker_name TEXT NOT NULL,
                item_description TEXT NOT NULL,
                size TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                price TEXT,
                cost TEXT,
                created_at TEXT NOT NULL,
                request_id TEXT,
                supplier_order_id INTEGER,
                is_custom INTEGER NOT NULL DEFAULT 0,
                kit_type TEXT,
                season TEXT,
                personalization TEXT,
                item_note TEXT,
                phone TEXT,
                item_image_url TEXT
            )
            """
        )
        db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_custom INTEGER NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS kit_type TEXT")
        db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS season TEXT")
        db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS personalization TEXT")
        db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS item_note TEXT")
        db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS phone TEXT")
        db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS item_image_url TEXT")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_items (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                size TEXT,
                price TEXT,
                available INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                image_url TEXT,
                category TEXT,
                kit_types TEXT,
                seasons TEXT
            )
            """
        )
        db.execute("ALTER TABLE catalog_items ADD COLUMN IF NOT EXISTS category TEXT")
        db.execute("ALTER TABLE catalog_items ADD COLUMN IF NOT EXISTS kit_types TEXT")
        db.execute("ALTER TABLE catalog_items ADD COLUMN IF NOT EXISTS seasons TEXT")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_item_images (
                id SERIAL PRIMARY KEY,
                catalog_item_id INTEGER NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
                kit_type TEXT NOT NULL,
                season TEXT NOT NULL,
                image_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (catalog_item_id, kit_type, season)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS supplier_orders (
                id SERIAL PRIMARY KEY,
                label TEXT,
                ordered_at TEXT,
                paid_at TEXT,
                shipped_at TEXT,
                received_at TEXT,
                created_at TEXT NOT NULL,
                shipping_cost TEXT
            )
            """
        )
        db.execute("ALTER TABLE supplier_orders ADD COLUMN IF NOT EXISTS shipping_cost TEXT")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_requests (
                id SERIAL PRIMARY KEY,
                request_id TEXT,
                coworker_name TEXT NOT NULL,
                description TEXT NOT NULL,
                admin_reply TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                phone TEXT
            )
            """
        )
        db.execute("ALTER TABLE custom_requests ADD COLUMN IF NOT EXISTS phone TEXT")
        db.execute(
            """
            UPDATE catalog_items
            SET category = 'Internacional',
                seasons = CASE
                    WHEN seasons IS NULL OR seasons = '' THEN category
                    WHEN position(category IN seasons) > 0 THEN seasons
                    ELSE seasons || ', ' || category
                END
            WHERE category IN ('Mundial 2026', 'Euro 2024', 'Outras Seleções')
            """
        )
        db.execute("UPDATE catalog_items SET size = replace(size, 'XXXL', '3XL') WHERE size LIKE '%XXXL%'")
        db.commit()


def parse_amount(text):
    if not text:
        return None
    match = re.search(r"-?\d+(\.\d+)?", text.replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def milestone_date(form, flag_name, existing_value, today):
    checked = form.get(flag_name) == "on"
    return (existing_value or today) if checked else None


def merge_custom_values(selected, custom_text):
    merged = list(selected)
    for v in (custom_text or "").split(","):
        v = v.strip()
        if v and v not in merged:
            merged.append(v)
    return merged


def save_uploaded_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return None

    unique_name = f"{uuid.uuid4().hex}.{ext}"
    file_bytes = file_storage.read()

    if os.environ.get("BLOB_READ_WRITE_TOKEN"):
        import vercel_blob

        result = vercel_blob.put(unique_name, file_bytes, {})
        return result["url"]

    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    (UPLOAD_FOLDER / unique_name).write_bytes(file_bytes)
    return f"uploads/{unique_name}"


def send_new_order_email(coworker_name, phone, notes, line_items, custom_request_text):
    api_key = os.environ.get("RESEND_API_KEY")
    notify_email = os.environ.get("NOTIFY_EMAIL")
    if not api_key or not notify_email:
        print(f"[email] skipped: RESEND_API_KEY set={bool(api_key)}, NOTIFY_EMAIL set={bool(notify_email)}")
        return

    rows_html = ""
    for line in line_items:
        details = ", ".join(
            filter(
                None,
                [
                    line["season"],
                    line["kit_type"],
                    line["size"] and f"tam. {line['size']}",
                    line["price_str"],
                    line["personalization"] and f"personalizar: {line['personalization']}",
                    line["item_note"],
                ],
            )
        )
        label = f"{line['name']} (fora do catálogo)" if line["is_custom"] else line["name"]
        rows_html += f"<li>{line['quantity']}x {label}" + (f" — {details}" if details else "") + "</li>"

    custom_html = f"<p><strong>Pergunta/pedido:</strong> {custom_request_text}</p>" if custom_request_text else ""
    notes_html = f"<p><strong>Notas:</strong> {notes}</p>" if notes else ""

    html = (
        f"<p><strong>{coworker_name}</strong> ({phone}) fez um novo pedido.</p>"
        f"<ul>{rows_html}</ul>"
        f"{custom_html}{notes_html}"
    )

    body = json.dumps(
        {
            "from": "PLR-Jerseys <onboarding@resend.dev>",
            "to": [notify_email],
            "subject": f"Novo pedido — {coworker_name}",
            "html": html,
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "PLR-Jerseys/1.0 (+https://plr-jerseys-six.vercel.app)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[email] sent, status={resp.status}, body={resp.read()}")
    except urllib.error.HTTPError as e:
        print(f"[email] HTTPError {e.code}: {e.read()}")
    except Exception as e:
        print(f"[email] failed: {type(e).__name__}: {e}")


def resolve_image_src(image_url):
    if not image_url:
        return None
    if image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url
    return url_for("static", filename=image_url)


@app.template_global()
def image_src(image_url):
    return resolve_image_src(image_url)


def enrich_catalog_items(db, catalog_rows):
    ids = [row["id"] for row in catalog_rows]
    variants_by_item = {}
    if ids:
        placeholders = ",".join(["%s"] * len(ids))
        variant_rows = db.execute(
            f"SELECT catalog_item_id, kit_type, season, image_url FROM catalog_item_images WHERE catalog_item_id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        for vr in variant_rows:
            key = f"{vr['kit_type']}|{vr['season']}"
            variants_by_item.setdefault(vr["catalog_item_id"], {})[key] = resolve_image_src(vr["image_url"])

    items = []
    for row in catalog_rows:
        item = dict(row)
        item["category"] = item["category"] or ""
        price = VINTAGE_PRICE if item["category"] == "Vintage" else BASE_PRICE
        item["display_price"] = f"{price:.0f}€"
        item["variant_images"] = variants_by_item.get(item["id"], {})
        items.append(item)
    return items


def group_catalog_by_category(catalog_items):
    by_category = {}
    for item in catalog_items:
        by_category.setdefault(item["category"] or "Outros", []).append(item)

    groups = [(cat, by_category.pop(cat)) for cat in CATEGORY_DISPLAY_ORDER if cat in by_category]
    for cat in sorted(by_category.keys()):
        groups.append((cat, by_category[cat]))
    return groups


def attach_variant_combos(db, catalog_rows):
    ids = [row["id"] for row in catalog_rows]
    variant_lookup = {}
    if ids:
        placeholders = ",".join(["%s"] * len(ids))
        for vr in db.execute(
            f"SELECT catalog_item_id, kit_type, season, image_url FROM catalog_item_images WHERE catalog_item_id IN ({placeholders})",
            tuple(ids),
        ).fetchall():
            variant_lookup[(vr["catalog_item_id"], vr["kit_type"], vr["season"])] = vr["image_url"]

    items = []
    for row in catalog_rows:
        item = dict(row)
        item_kit_types = [k.strip() for k in (item["kit_types"] or "").split(",") if k.strip()]
        item_seasons = [s.strip() for s in (item["seasons"] or "").split(",") if s.strip()]
        combos = []
        for kt in item_kit_types:
            for se in item_seasons:
                combos.append(
                    {
                        "kit_type": kt,
                        "season": se,
                        "image_url": variant_lookup.get((item["id"], kt, se)),
                    }
                )
        item["variant_combos"] = combos
        items.append(item)
    return items


@app.route("/")
def index():
    db = get_db()
    catalog = enrich_catalog_items(
        db,
        db.execute(
            "SELECT * FROM catalog_items WHERE available = 1 ORDER BY category ASC NULLS LAST, name ASC"
        ).fetchall(),
    )
    return render_template("index.html", catalog=catalog, catalog_groups=group_catalog_by_category(catalog))


def is_group_editable(group):
    return bool(group["lines"]) and all(line["status"] == "pending" for line in group["lines"])


@app.route("/as-minhas-encomendas", methods=["GET", "POST"])
def my_orders():
    phone = (request.form.get("phone") if request.method == "POST" else request.args.get("phone", "")).strip()
    if not phone:
        error = "Escreve o teu telemóvel." if request.method == "POST" else None
        return render_template("my_orders.html", searched=False, order_groups=None, error=error)

    db = get_db()
    order_groups, _ = build_order_groups(db, phone=phone)
    for group in order_groups:
        group["editable"] = is_group_editable(group)

    return render_template(
        "my_orders.html",
        searched=True,
        order_groups=order_groups,
        phone=phone,
        status_labels=STATUS_LABELS,
        custom_request_status_labels=CUSTOM_REQUEST_STATUS_LABELS,
    )


def parse_order_form(form, files, catalog_by_name):
    """Reads + validates the order form. Returns a dict with the parsed fields on
    success, or a dict with only an "error" key on failure."""
    coworker_name = form.get("coworker_name", "").strip()
    phone = form.get("phone", "").strip()
    notes = form.get("notes", "").strip()
    custom_request_text = form.get("custom_request", "").strip()
    item_names = form.getlist("item_description[]")
    seasons = form.getlist("season[]")
    season_custom_flags = form.getlist("season_is_custom[]")
    kit_types = form.getlist("kit_type[]")
    kit_type_custom_flags = form.getlist("kit_type_is_custom[]")
    sizes = form.getlist("size[]")
    quantities = form.getlist("quantity[]")
    personalize_flags = form.getlist("personalize[]")
    personalize_texts = form.getlist("personalize_text[]")
    item_notes = form.getlist("item_note[]")
    item_images = files.getlist("item_image[]")

    line_items = []
    for i, raw_name in enumerate(item_names):
        name = raw_name.strip()
        season = seasons[i].strip() if i < len(seasons) else ""
        season_is_custom = (season_custom_flags[i].strip() == "1") if i < len(season_custom_flags) else False
        kit_type = kit_types[i].strip() if i < len(kit_types) else ""
        kit_type_is_custom = (kit_type_custom_flags[i].strip() == "1") if i < len(kit_type_custom_flags) else False
        size = sizes[i].strip() if i < len(sizes) else ""
        personalized = (personalize_flags[i].strip() == "1") if i < len(personalize_flags) else False
        personalization = personalize_texts[i].strip() if i < len(personalize_texts) else ""
        if not personalized:
            personalization = ""
        item_note = item_notes[i].strip() if i < len(item_notes) else ""
        if not name and not size:
            continue

        matched_item = catalog_by_name.get(name)
        if matched_item:
            valid_sizes = [s.strip() for s in (matched_item["size"] or "").split(",") if s.strip()]
            if valid_sizes and size not in valid_sizes:
                return {"error": "Um dos artigos ou tamanhos não é válido — volta a escolher da lista."}
            if not valid_sizes:
                size = ""
            if kit_type_is_custom:
                if not kit_type:
                    return {"error": "Escreve o tipo que procuras, ou desmarca a opção de tipo personalizado."}
            else:
                valid_kit_types = [k.strip() for k in (matched_item["kit_types"] or "").split(",") if k.strip()]
                if valid_kit_types and kit_type not in valid_kit_types:
                    return {"error": "Um dos tipos de camisola não é válido — volta a escolher da lista."}
                if not valid_kit_types:
                    kit_type = ""
            if season_is_custom:
                if not season:
                    return {"error": "Escreve a temporada que procuras, ou desmarca a opção de temporada personalizada."}
            else:
                valid_seasons = [s.strip() for s in (matched_item["seasons"] or "").split(",") if s.strip()]
                if valid_seasons and season not in valid_seasons:
                    return {"error": "Uma das temporadas não é válida — volta a escolher da lista."}
                if not valid_seasons:
                    season = ""
            if personalized and not personalization:
                return {"error": "Escreve o nome/número a personalizar, ou desmarca a personalização."}
            if matched_item["category"] == "Vintage" and not item_note:
                return {"error": "Escreve o que procuras no artigo Vintage (equipa, ano, jogador...)."}
            if matched_item["category"] != "Vintage":
                item_note = ""
            base_price = VINTAGE_PRICE if matched_item["category"] == "Vintage" else BASE_PRICE
            price = base_price + (PERSONALIZATION_PRICE if personalized else 0)
            price_str = f"{price:.2f}€"
            is_custom = False
        else:
            if not name or not size:
                return {"error": "Preenche o nome e o tamanho do artigo personalizado."}
            season = ""
            kit_type = ""
            price_str = None
            is_custom = True
            item_note = ""

        try:
            quantity = max(1, int(quantities[i].strip())) if i < len(quantities) else 1
        except ValueError:
            quantity = 1

        image_file = item_images[i] if i < len(item_images) else None
        item_image_url = save_uploaded_image(image_file)

        line_items.append(
            {
                "name": name,
                "season": season,
                "kit_type": kit_type,
                "size": size,
                "quantity": quantity,
                "is_custom": is_custom,
                "price_str": price_str,
                "personalization": personalization,
                "item_note": item_note,
                "item_image_url": item_image_url,
            }
        )

    if not coworker_name or not phone or (not line_items and not custom_request_text):
        return {"error": "Preenche o teu nome, o teu telemóvel, e escolhe um artigo ou descreve o que procuras."}

    return {
        "coworker_name": coworker_name,
        "phone": phone,
        "notes": notes,
        "custom_request_text": custom_request_text,
        "line_items": line_items,
    }


@app.route("/order", methods=["POST"])
def create_order():
    db = get_db()
    catalog = enrich_catalog_items(
        db,
        db.execute(
            "SELECT * FROM catalog_items WHERE available = 1 ORDER BY category ASC NULLS LAST, name ASC"
        ).fetchall(),
    )
    catalog_by_name = {c["name"]: c for c in catalog}
    catalog_groups = group_catalog_by_category(catalog)

    parsed = parse_order_form(request.form, request.files, catalog_by_name)
    if "error" in parsed:
        return render_template(
            "index.html", error=parsed["error"], catalog=catalog, catalog_groups=catalog_groups
        )

    coworker_name = parsed["coworker_name"]
    phone = parsed["phone"]
    notes = parsed["notes"]
    custom_request_text = parsed["custom_request_text"]
    line_items = parsed["line_items"]

    request_id = uuid.uuid4().hex[:8]
    created_at = datetime.now().isoformat(timespec="seconds")
    for line in line_items:
        db.execute(
            """
            INSERT INTO orders
                (coworker_name, item_description, season, kit_type, size, quantity, notes, status,
                 price, created_at, request_id, is_custom, personalization, item_note, phone, item_image_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                coworker_name,
                line["name"],
                line["season"] or None,
                line["kit_type"] or None,
                line["size"],
                line["quantity"],
                notes,
                line["price_str"],
                created_at,
                request_id,
                1 if line["is_custom"] else 0,
                line["personalization"] or None,
                line["item_note"] or None,
                phone,
                line["item_image_url"],
            ),
        )

    if custom_request_text:
        db.execute(
            """
            INSERT INTO custom_requests (request_id, coworker_name, description, status, created_at, phone)
            VALUES (%s, %s, %s, 'pending', %s, %s)
            """,
            (request_id, coworker_name, custom_request_text, created_at, phone),
        )

    db.commit()

    send_new_order_email(coworker_name, phone, notes, line_items, custom_request_text)

    return render_template("index.html", success=True, catalog=catalog, catalog_groups=catalog_groups)


def _find_own_group(db, request_id, phone):
    if not phone:
        return None
    order_groups, _ = build_order_groups(db, phone=phone)
    return next((g for g in order_groups if g["request_id"] == request_id), None)


def _build_edit_lines(group_lines):
    return [
        {
            "is_custom": bool(line["is_custom"]),
            "item_description": line["item_description"],
            "season": line["season"] or "",
            "kit_type": line["kit_type"] or "",
            "size": line["size"] or "",
            "quantity": line["quantity"],
            "personalization": line["personalization"] or "",
            "item_note": line["item_note"] or "",
            "item_image_url": resolve_image_src(line["item_image_url"]),
        }
        for line in group_lines
    ]


@app.route("/pedido/<request_id>/editar", methods=["GET"])
def edit_order(request_id):
    phone = request.args.get("phone", "").strip()
    db = get_db()
    group = _find_own_group(db, request_id, phone)
    if not group or not is_group_editable(group):
        return redirect(url_for("my_orders", phone=phone))

    catalog = enrich_catalog_items(
        db,
        db.execute(
            "SELECT * FROM catalog_items WHERE available = 1 ORDER BY category ASC NULLS LAST, name ASC"
        ).fetchall(),
    )
    edit_lines = _build_edit_lines(group["lines"])

    return render_template(
        "edit_order.html",
        catalog=catalog,
        catalog_groups=group_catalog_by_category(catalog),
        group=group,
        phone=phone,
        request_id=request_id,
        edit_lines=edit_lines,
    )


@app.route("/pedido/<request_id>/editar", methods=["POST"])
def update_own_order(request_id):
    phone = request.form.get("phone", "").strip()
    db = get_db()
    group = _find_own_group(db, request_id, phone)
    if not group or not is_group_editable(group):
        return redirect(url_for("my_orders", phone=phone))

    catalog = enrich_catalog_items(
        db,
        db.execute(
            "SELECT * FROM catalog_items WHERE available = 1 ORDER BY category ASC NULLS LAST, name ASC"
        ).fetchall(),
    )
    catalog_by_name = {c["name"]: c for c in catalog}
    catalog_groups = group_catalog_by_category(catalog)

    parsed = parse_order_form(request.form, request.files, catalog_by_name)
    if "error" in parsed:
        return render_template(
            "edit_order.html",
            error=parsed["error"],
            catalog=catalog,
            catalog_groups=catalog_groups,
            group=group,
            phone=phone,
            request_id=request_id,
            edit_lines=_build_edit_lines(group["lines"]),
        )

    if parsed["phone"] != phone:
        return render_template(
            "edit_order.html",
            error="O telemóvel não pode ser alterado aqui — contacta o administrador.",
            catalog=catalog,
            catalog_groups=catalog_groups,
            group=group,
            phone=phone,
            request_id=request_id,
            edit_lines=_build_edit_lines(group["lines"]),
        )

    coworker_name = parsed["coworker_name"]
    notes = parsed["notes"]
    custom_request_text = parsed["custom_request_text"]
    line_items = parsed["line_items"]
    created_at = group["created_at"]

    db.execute("DELETE FROM orders WHERE request_id = %s", (request_id,))
    db.execute("DELETE FROM custom_requests WHERE request_id = %s", (request_id,))

    for line in line_items:
        db.execute(
            """
            INSERT INTO orders
                (coworker_name, item_description, season, kit_type, size, quantity, notes, status,
                 price, created_at, request_id, is_custom, personalization, item_note, phone, item_image_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                coworker_name,
                line["name"],
                line["season"] or None,
                line["kit_type"] or None,
                line["size"],
                line["quantity"],
                notes,
                line["price_str"],
                created_at,
                request_id,
                1 if line["is_custom"] else 0,
                line["personalization"] or None,
                line["item_note"] or None,
                phone,
                line["item_image_url"],
            ),
        )

    if custom_request_text:
        db.execute(
            """
            INSERT INTO custom_requests (request_id, coworker_name, description, status, created_at, phone)
            VALUES (%s, %s, %s, 'pending', %s, %s)
            """,
            (request_id, coworker_name, custom_request_text, created_at, phone),
        )

    db.commit()

    return redirect(url_for("my_orders", phone=phone))


@app.route("/pedido/<request_id>/cancelar", methods=["POST"])
def cancel_order(request_id):
    phone = request.form.get("phone", "").strip()
    db = get_db()
    group = _find_own_group(db, request_id, phone)
    if group and is_group_editable(group):
        db.execute("UPDATE orders SET status = 'cancelled' WHERE request_id = %s", (request_id,))
        db.commit()
    return redirect(url_for("my_orders", phone=phone))


@app.route("/admin")
def admin_catalog():
    db = get_db()
    catalog = attach_variant_combos(
        db, db.execute("SELECT * FROM catalog_items ORDER BY category ASC NULLS LAST, name ASC").fetchall()
    )
    return render_template(
        "admin_catalog.html",
        catalog=catalog,
        sizes=SIZES,
        categories=CATEGORIES,
        kit_types=KIT_TYPES,
        seasons=SEASONS,
        base_price=BASE_PRICE,
        vintage_price=VINTAGE_PRICE,
        personalization_price=PERSONALIZATION_PRICE,
    )


def build_order_groups(db, phone=None):
    """Groups orders (+ any linked custom request) by request_id. When phone is
    given, only that phone's orders/requests are included — used by the
    self-service "as minhas encomendas" page; admin calls this with phone=None."""
    if phone:
        rows = db.execute(
            "SELECT * FROM orders WHERE phone = %s ORDER BY created_at DESC, id ASC", (phone,)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC, id ASC").fetchall()

    groups = {}
    for _, group_rows in groupby(rows, key=lambda o: o["request_id"] or f"single-{o['id']}"):
        items = []
        total_price = 0.0
        total_cost = 0.0
        priced_count = 0
        costed_count = 0

        for row in group_rows:
            item = dict(row)
            price_val = parse_amount(row["price"])
            cost_val = parse_amount(row["cost"])
            if price_val is not None:
                total_price += price_val
                priced_count += 1
            if cost_val is not None:
                total_cost += cost_val
                costed_count += 1
            item["margin"] = (
                f"{price_val - cost_val:.2f}" if price_val is not None and cost_val is not None else None
            )
            items.append(item)

        first = items[0]
        key = first["request_id"] or f"single-{first['id']}"
        all_priced = priced_count == len(items)
        all_costed = costed_count == len(items)
        groups[key] = {
            "request_id": first["request_id"],
            "coworker_name": first["coworker_name"],
            "phone": first["phone"],
            "created_at": first["created_at"],
            "notes": first["notes"],
            "lines": items,
            "custom_request": None,
            "total_price": total_price if priced_count else None,
            "total_price_partial": priced_count and not all_priced,
            "total_cost": total_cost if costed_count else None,
            "total_cost_partial": costed_count and not all_costed,
            "total_margin": (total_price - total_cost) if all_priced and all_costed else None,
        }

    if phone:
        custom_rows = db.execute(
            "SELECT * FROM custom_requests WHERE phone = %s ORDER BY created_at DESC, id ASC", (phone,)
        ).fetchall()
    else:
        custom_rows = db.execute("SELECT * FROM custom_requests ORDER BY created_at DESC, id ASC").fetchall()
    for row in custom_rows:
        key = row["request_id"] or f"custom-{row['id']}"
        if key in groups:
            groups[key]["custom_request"] = dict(row)
        else:
            groups[key] = {
                "request_id": row["request_id"],
                "coworker_name": row["coworker_name"],
                "phone": row["phone"],
                "created_at": row["created_at"],
                "notes": None,
                "lines": [],
                "custom_request": dict(row),
                "total_price": None,
                "total_price_partial": False,
                "total_cost": None,
                "total_cost_partial": False,
                "total_margin": None,
            }

    order_groups = sorted(groups.values(), key=lambda g: g["created_at"], reverse=True)
    return order_groups, rows


@app.route("/admin/encomendas")
def admin_orders():
    db = get_db()
    order_groups, rows = build_order_groups(db)

    supplier_order_rows = db.execute("SELECT * FROM supplier_orders ORDER BY created_at DESC").fetchall()
    linked_by_supplier = {}
    for o in rows:
        if o["supplier_order_id"] is not None:
            linked_by_supplier.setdefault(o["supplier_order_id"], []).append(o)

    supplier_orders = []
    for so in supplier_order_rows:
        linked = linked_by_supplier.get(so["id"], [])
        so_total_cost = 0.0
        so_total_price = 0.0
        so_costed = 0
        so_priced = 0
        for o in linked:
            cost_val = parse_amount(o["cost"])
            price_val = parse_amount(o["price"])
            if cost_val is not None:
                so_total_cost += cost_val
                so_costed += 1
            if price_val is not None:
                so_total_price += price_val
                so_priced += 1
        shipping_cost_val = parse_amount(so["shipping_cost"])
        total_margin = (
            (so_total_price - so_total_cost)
            if linked and so_costed == len(linked) and so_priced == len(linked)
            else None
        )
        supplier_orders.append(
            {
                "id": so["id"],
                "label": so["label"],
                "ordered_at": so["ordered_at"],
                "paid_at": so["paid_at"],
                "shipped_at": so["shipped_at"],
                "received_at": so["received_at"],
                "shipping_cost": so["shipping_cost"],
                "linked": linked,
                "total_cost": so_total_cost if so_costed else None,
                "total_cost_partial": bool(so_costed) and so_costed < len(linked),
                "total_price": so_total_price if so_priced else None,
                "total_price_partial": bool(so_priced) and so_priced < len(linked),
                "total_margin": total_margin,
                "real_margin": (
                    (total_margin - shipping_cost_val)
                    if total_margin is not None and shipping_cost_val is not None
                    else None
                ),
            }
        )

    return render_template(
        "admin_orders.html",
        order_groups=order_groups,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        supplier_orders=supplier_orders,
        custom_request_statuses=CUSTOM_REQUEST_STATUSES,
        custom_request_status_labels=CUSTOM_REQUEST_STATUS_LABELS,
    )


@app.route("/admin/catalog/bulk_add", methods=["POST"])
def bulk_add_catalog_items():
    bulk_text = request.form.get("bulk_text", "")
    created_at = datetime.now().isoformat(timespec="seconds")

    db = get_db()
    for line in bulk_text.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split("|")]
        category = parts[0] if parts and parts[0] in CATEGORIES else ""
        name = parts[1] if len(parts) > 1 else ""
        if not name:
            continue

        seasons = [s.strip() for s in parts[2].split(",") if s.strip() in SEASONS] if len(parts) > 2 else []
        kit_types = [k.strip() for k in parts[3].split(",") if k.strip() in KIT_TYPES] if len(parts) > 3 else []
        sizes = [s.strip() for s in parts[4].split(",") if s.strip() in SIZES] if len(parts) > 4 else []

        db.execute(
            """
            INSERT INTO catalog_items (name, size, available, created_at, category, kit_types, seasons)
            VALUES (%s, %s, 1, %s, %s, %s, %s)
            """,
            (name, ", ".join(sizes), created_at, category or None, ", ".join(kit_types), ", ".join(seasons)),
        )
    db.commit()

    return redirect(url_for("admin_catalog"))


@app.route("/admin/catalog/bulk_edit", methods=["POST"])
def bulk_edit_catalog_items():
    bulk_text = request.form.get("bulk_edit_text", "")

    db = get_db()
    for line in bulk_text.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue

        try:
            item_id = int(parts[0])
        except ValueError:
            continue

        available = 1 if parts[1].strip().lower() in ("sim", "s", "1", "yes") else 0
        category = parts[2] if parts[2] in CATEGORIES else ""
        name = parts[3]
        if not name:
            continue

        seasons = [s.strip() for s in parts[4].split(",") if s.strip() in SEASONS] if len(parts) > 4 else []
        kit_types = [k.strip() for k in parts[5].split(",") if k.strip() in KIT_TYPES] if len(parts) > 5 else []
        sizes = [s.strip() for s in parts[6].split(",") if s.strip() in SIZES] if len(parts) > 6 else []

        db.execute(
            """
            UPDATE catalog_items
            SET name = %s, size = %s, available = %s, category = %s, kit_types = %s, seasons = %s
            WHERE id = %s
            """,
            (name, ", ".join(sizes), available, category or None, ", ".join(kit_types), ", ".join(seasons), item_id),
        )
    db.commit()

    return redirect(url_for("admin_catalog"))


@app.route("/admin/catalog/add", methods=["POST"])
def add_catalog_item():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    seasons = [s for s in request.form.getlist("seasons") if s in SEASONS]
    seasons = merge_custom_values(seasons, request.form.get("custom_seasons"))
    kit_types = [k for k in request.form.getlist("kit_types") if k in KIT_TYPES]
    sizes = [s for s in request.form.getlist("sizes") if s in SIZES]
    image_url = save_uploaded_image(request.files.get("image"))

    if name:
        db = get_db()
        db.execute(
            """
            INSERT INTO catalog_items (name, size, available, created_at, image_url, category, kit_types, seasons)
            VALUES (%s, %s, 1, %s, %s, %s, %s, %s)
            """,
            (
                name,
                ", ".join(sizes),
                datetime.now().isoformat(timespec="seconds"),
                image_url,
                category or None,
                ", ".join(kit_types),
                ", ".join(seasons),
            ),
        )
        db.commit()

    return redirect(url_for("admin_catalog"))


@app.route("/admin/catalog/update/<int:item_id>", methods=["POST"])
def update_catalog_item(item_id):
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    seasons = [s for s in request.form.getlist("seasons") if s in SEASONS]
    seasons = merge_custom_values(seasons, request.form.get("custom_seasons"))
    kit_types = [k for k in request.form.getlist("kit_types") if k in KIT_TYPES]
    sizes = [s for s in request.form.getlist("sizes") if s in SIZES]
    available = 1 if request.form.get("available") == "on" else 0

    db = get_db()
    existing = db.execute("SELECT * FROM catalog_items WHERE id = %s", (item_id,)).fetchone()
    new_image = save_uploaded_image(request.files.get("image"))
    image_url = new_image or (existing["image_url"] if existing else None)

    db.execute(
        """
        UPDATE catalog_items
        SET name = %s, size = %s, available = %s, image_url = %s, category = %s, kit_types = %s, seasons = %s
        WHERE id = %s
        """,
        (
            name,
            ", ".join(sizes),
            available,
            image_url,
            category or None,
            ", ".join(kit_types),
            ", ".join(seasons),
            item_id,
        ),
    )

    variant_kit_types = request.form.getlist("variant_kit_type[]")
    variant_seasons = request.form.getlist("variant_season[]")
    variant_files = request.files.getlist("variant_image[]")
    for i, vk in enumerate(variant_kit_types):
        vs = variant_seasons[i] if i < len(variant_seasons) else ""
        vf = variant_files[i] if i < len(variant_files) else None
        if not vk or not vs:
            continue
        variant_image_url = save_uploaded_image(vf)
        if not variant_image_url:
            continue
        db.execute(
            """
            INSERT INTO catalog_item_images (catalog_item_id, kit_type, season, image_url, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (catalog_item_id, kit_type, season)
            DO UPDATE SET image_url = EXCLUDED.image_url
            """,
            (item_id, vk, vs, variant_image_url, datetime.now().isoformat(timespec="seconds")),
        )

    db.commit()
    return redirect(url_for("admin_catalog"))


@app.route("/admin/catalog/delete/<int:item_id>", methods=["POST"])
def delete_catalog_item(item_id):
    db = get_db()
    db.execute("DELETE FROM catalog_items WHERE id = %s", (item_id,))
    db.commit()
    return redirect(url_for("admin_catalog"))


@app.route("/admin/update/<int:order_id>", methods=["POST"])
def update_order(order_id):
    status = request.form.get("status")
    price = request.form.get("price", "").strip()
    cost = request.form.get("cost", "").strip()
    supplier_order_raw = request.form.get("supplier_order_id", "").strip()

    if status not in STATUSES:
        return redirect(url_for("admin_orders"))

    supplier_order_id = int(supplier_order_raw) if supplier_order_raw.isdigit() else None

    db = get_db()
    db.execute(
        "UPDATE orders SET status = %s, price = %s, cost = %s, supplier_order_id = %s WHERE id = %s",
        (status, price, cost, supplier_order_id, order_id),
    )
    db.commit()
    return redirect(url_for("admin_orders"))


@app.route("/admin/delete/<int:order_id>", methods=["POST"])
def delete_order(order_id):
    db = get_db()
    db.execute("DELETE FROM orders WHERE id = %s", (order_id,))
    db.commit()
    return redirect(url_for("admin_orders"))


@app.route("/admin/custom_requests/update/<int:request_id>", methods=["POST"])
def update_custom_request(request_id):
    status = request.form.get("status", "pending")
    admin_reply = request.form.get("admin_reply", "").strip()

    if status not in CUSTOM_REQUEST_STATUSES:
        status = "pending"

    db = get_db()
    db.execute(
        "UPDATE custom_requests SET status = %s, admin_reply = %s WHERE id = %s",
        (status, admin_reply, request_id),
    )
    db.commit()
    return redirect(url_for("admin_orders"))


@app.route("/admin/custom_requests/delete/<int:request_id>", methods=["POST"])
def delete_custom_request(request_id):
    db = get_db()
    db.execute("DELETE FROM custom_requests WHERE id = %s", (request_id,))
    db.commit()
    return redirect(url_for("admin_orders"))


@app.route("/admin/custom_requests/promote/<int:request_id>", methods=["POST"])
def promote_custom_request(request_id):
    db = get_db()
    row = db.execute("SELECT * FROM custom_requests WHERE id = %s", (request_id,)).fetchone()
    if row is None:
        return redirect(url_for("admin_orders"))

    db.execute(
        """
        INSERT INTO catalog_items (name, size, available, created_at)
        VALUES (%s, '', 0, %s)
        """,
        (row["description"], datetime.now().isoformat(timespec="seconds")),
    )
    db.execute(
        "UPDATE custom_requests SET status = 'answered', admin_reply = %s WHERE id = %s",
        (row["admin_reply"] or "Adicionado ao catálogo.", request_id),
    )
    db.commit()
    return redirect(url_for("admin_orders"))


@app.route("/admin/supplier_orders/add", methods=["POST"])
def add_supplier_order():
    label = request.form.get("label", "").strip()
    db = get_db()
    db.execute(
        "INSERT INTO supplier_orders (label, created_at) VALUES (%s, %s)",
        (label or None, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    return redirect(url_for("admin_orders"))


@app.route("/admin/supplier_orders/update/<int:supplier_order_id>", methods=["POST"])
def update_supplier_order(supplier_order_id):
    label = request.form.get("label", "").strip()
    shipping_cost = request.form.get("shipping_cost", "").strip()

    db = get_db()
    existing = db.execute("SELECT * FROM supplier_orders WHERE id = %s", (supplier_order_id,)).fetchone()
    if existing is None:
        return redirect(url_for("admin_orders"))

    today = datetime.now().date().isoformat()
    ordered_at = milestone_date(request.form, "ordered", existing["ordered_at"], today)
    paid_at = milestone_date(request.form, "paid", existing["paid_at"], today)
    shipped_at = milestone_date(request.form, "shipped", existing["shipped_at"], today)
    received_at = milestone_date(request.form, "received", existing["received_at"], today)

    db.execute(
        """
        UPDATE supplier_orders
        SET label = %s, ordered_at = %s, paid_at = %s, shipped_at = %s, received_at = %s, shipping_cost = %s
        WHERE id = %s
        """,
        (label or None, ordered_at, paid_at, shipped_at, received_at, shipping_cost or None, supplier_order_id),
    )
    db.commit()
    return redirect(url_for("admin_orders"))


@app.route("/admin/supplier_orders/delete/<int:supplier_order_id>", methods=["POST"])
def delete_supplier_order(supplier_order_id):
    db = get_db()
    db.execute("UPDATE orders SET supplier_order_id = NULL WHERE supplier_order_id = %s", (supplier_order_id,))
    db.execute("DELETE FROM supplier_orders WHERE id = %s", (supplier_order_id,))
    db.commit()
    return redirect(url_for("admin_orders"))


with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=True)
