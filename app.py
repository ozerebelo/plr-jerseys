import os
import re
import secrets
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

STATUSES = ["pending", "confirmed", "paid", "fulfilled", "rejected"]
STATUS_LABELS = {
    "pending": "Pendente",
    "confirmed": "Confirmado",
    "paid": "Pago",
    "fulfilled": "Entregue",
    "rejected": "Rejeitado",
}
SIZES = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]
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
                is_custom INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_custom INTEGER NOT NULL DEFAULT 0")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_items (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                size TEXT,
                price TEXT,
                available INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                image_url TEXT
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
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_requests (
                id SERIAL PRIMARY KEY,
                request_id TEXT,
                coworker_name TEXT NOT NULL,
                description TEXT NOT NULL,
                admin_reply TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )
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


@app.template_global()
def image_src(image_url):
    if not image_url:
        return None
    if image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url
    return url_for("static", filename=image_url)


@app.route("/")
def index():
    db = get_db()
    catalog = db.execute(
        "SELECT * FROM catalog_items WHERE available = 1 ORDER BY name ASC"
    ).fetchall()
    return render_template("index.html", catalog=catalog)


@app.route("/order", methods=["POST"])
def create_order():
    coworker_name = request.form.get("coworker_name", "").strip()
    notes = request.form.get("notes", "").strip()
    custom_request_text = request.form.get("custom_request", "").strip()
    item_names = request.form.getlist("item_description[]")
    sizes = request.form.getlist("size[]")
    quantities = request.form.getlist("quantity[]")

    db = get_db()
    catalog = db.execute(
        "SELECT * FROM catalog_items WHERE available = 1 ORDER BY name ASC"
    ).fetchall()
    catalog_by_name = {c["name"]: c for c in catalog}

    line_items = []
    for i, raw_name in enumerate(item_names):
        name = raw_name.strip()
        size = sizes[i].strip() if i < len(sizes) else ""
        if not name and not size:
            continue

        matched_item = catalog_by_name.get(name)
        if matched_item:
            valid_sizes = [s.strip() for s in (matched_item["size"] or "").split(",") if s.strip()]
            if size not in valid_sizes:
                return render_template(
                    "index.html",
                    error="Um dos artigos ou tamanhos não é válido — volta a escolher da lista.",
                    catalog=catalog,
                )
            is_custom = False
        else:
            if not name or not size:
                return render_template(
                    "index.html",
                    error="Preenche o nome e o tamanho do artigo personalizado.",
                    catalog=catalog,
                )
            is_custom = True

        try:
            quantity = max(1, int(quantities[i].strip())) if i < len(quantities) else 1
        except ValueError:
            quantity = 1

        line_items.append((name, size, quantity, is_custom))

    if not coworker_name or (not line_items and not custom_request_text):
        return render_template(
            "index.html",
            error="Preenche o teu nome e escolhe um artigo ou descreve o que procuras.",
            catalog=catalog,
        )

    request_id = uuid.uuid4().hex[:8]
    created_at = datetime.now().isoformat(timespec="seconds")
    for name, size, quantity, is_custom in line_items:
        db.execute(
            """
            INSERT INTO orders (coworker_name, item_description, size, quantity, notes, status, created_at, request_id, is_custom)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s, %s)
            """,
            (coworker_name, name, size, quantity, notes, created_at, request_id, 1 if is_custom else 0),
        )

    if custom_request_text:
        db.execute(
            """
            INSERT INTO custom_requests (request_id, coworker_name, description, status, created_at)
            VALUES (%s, %s, %s, 'pending', %s)
            """,
            (request_id, coworker_name, custom_request_text, created_at),
        )

    db.commit()

    return render_template("index.html", success=True, catalog=catalog)


@app.route("/admin")
def admin():
    db = get_db()
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

    custom_rows = db.execute("SELECT * FROM custom_requests ORDER BY created_at DESC, id ASC").fetchall()
    for row in custom_rows:
        key = row["request_id"] or f"custom-{row['id']}"
        if key in groups:
            groups[key]["custom_request"] = dict(row)
        else:
            groups[key] = {
                "request_id": row["request_id"],
                "coworker_name": row["coworker_name"],
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
        supplier_orders.append(
            {
                "id": so["id"],
                "label": so["label"],
                "ordered_at": so["ordered_at"],
                "paid_at": so["paid_at"],
                "shipped_at": so["shipped_at"],
                "received_at": so["received_at"],
                "linked": linked,
                "total_cost": so_total_cost if so_costed else None,
                "total_cost_partial": bool(so_costed) and so_costed < len(linked),
                "total_price": so_total_price if so_priced else None,
                "total_price_partial": bool(so_priced) and so_priced < len(linked),
                "total_margin": (
                    (so_total_price - so_total_cost)
                    if linked and so_costed == len(linked) and so_priced == len(linked)
                    else None
                ),
            }
        )

    catalog = db.execute("SELECT * FROM catalog_items ORDER BY name ASC").fetchall()
    return render_template(
        "admin.html",
        order_groups=order_groups,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        catalog=catalog,
        sizes=SIZES,
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
        name = parts[0] if parts else ""
        if not name:
            continue

        sizes = [s.strip() for s in parts[1].split(",") if s.strip() in SIZES] if len(parts) > 1 else []
        price = parts[2] if len(parts) > 2 else ""

        db.execute(
            """
            INSERT INTO catalog_items (name, size, price, available, created_at)
            VALUES (%s, %s, %s, 1, %s)
            """,
            (name, ", ".join(sizes), price, created_at),
        )
    db.commit()

    return redirect(url_for("admin"))


@app.route("/admin/catalog/add", methods=["POST"])
def add_catalog_item():
    name = request.form.get("name", "").strip()
    sizes = [s for s in request.form.getlist("sizes") if s in SIZES]
    price = request.form.get("price", "").strip()
    image_url = save_uploaded_image(request.files.get("image"))

    if name:
        db = get_db()
        db.execute(
            """
            INSERT INTO catalog_items (name, size, price, available, created_at, image_url)
            VALUES (%s, %s, %s, 1, %s, %s)
            """,
            (name, ", ".join(sizes), price, datetime.now().isoformat(timespec="seconds"), image_url),
        )
        db.commit()

    return redirect(url_for("admin"))


@app.route("/admin/catalog/update/<int:item_id>", methods=["POST"])
def update_catalog_item(item_id):
    name = request.form.get("name", "").strip()
    sizes = [s for s in request.form.getlist("sizes") if s in SIZES]
    price = request.form.get("price", "").strip()
    available = 1 if request.form.get("available") == "on" else 0

    db = get_db()
    existing = db.execute("SELECT * FROM catalog_items WHERE id = %s", (item_id,)).fetchone()
    new_image = save_uploaded_image(request.files.get("image"))
    image_url = new_image or (existing["image_url"] if existing else None)

    db.execute(
        "UPDATE catalog_items SET name = %s, size = %s, price = %s, available = %s, image_url = %s WHERE id = %s",
        (name, ", ".join(sizes), price, available, image_url, item_id),
    )
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/catalog/delete/<int:item_id>", methods=["POST"])
def delete_catalog_item(item_id):
    db = get_db()
    db.execute("DELETE FROM catalog_items WHERE id = %s", (item_id,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/update/<int:order_id>", methods=["POST"])
def update_order(order_id):
    status = request.form.get("status")
    price = request.form.get("price", "").strip()
    cost = request.form.get("cost", "").strip()
    supplier_order_raw = request.form.get("supplier_order_id", "").strip()

    if status not in STATUSES:
        return redirect(url_for("admin"))

    supplier_order_id = int(supplier_order_raw) if supplier_order_raw.isdigit() else None

    db = get_db()
    db.execute(
        "UPDATE orders SET status = %s, price = %s, cost = %s, supplier_order_id = %s WHERE id = %s",
        (status, price, cost, supplier_order_id, order_id),
    )
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/delete/<int:order_id>", methods=["POST"])
def delete_order(order_id):
    db = get_db()
    db.execute("DELETE FROM orders WHERE id = %s", (order_id,))
    db.commit()
    return redirect(url_for("admin"))


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
    return redirect(url_for("admin"))


@app.route("/admin/custom_requests/delete/<int:request_id>", methods=["POST"])
def delete_custom_request(request_id):
    db = get_db()
    db.execute("DELETE FROM custom_requests WHERE id = %s", (request_id,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/custom_requests/promote/<int:request_id>", methods=["POST"])
def promote_custom_request(request_id):
    db = get_db()
    row = db.execute("SELECT * FROM custom_requests WHERE id = %s", (request_id,)).fetchone()
    if row is None:
        return redirect(url_for("admin"))

    db.execute(
        """
        INSERT INTO catalog_items (name, size, price, available, created_at)
        VALUES (%s, '', '', 0, %s)
        """,
        (row["description"], datetime.now().isoformat(timespec="seconds")),
    )
    db.execute(
        "UPDATE custom_requests SET status = 'answered', admin_reply = %s WHERE id = %s",
        (row["admin_reply"] or "Adicionado ao catálogo.", request_id),
    )
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/supplier_orders/add", methods=["POST"])
def add_supplier_order():
    label = request.form.get("label", "").strip()
    db = get_db()
    db.execute(
        "INSERT INTO supplier_orders (label, created_at) VALUES (%s, %s)",
        (label or None, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/supplier_orders/update/<int:supplier_order_id>", methods=["POST"])
def update_supplier_order(supplier_order_id):
    label = request.form.get("label", "").strip()

    db = get_db()
    existing = db.execute("SELECT * FROM supplier_orders WHERE id = %s", (supplier_order_id,)).fetchone()
    if existing is None:
        return redirect(url_for("admin"))

    today = datetime.now().date().isoformat()
    ordered_at = milestone_date(request.form, "ordered", existing["ordered_at"], today)
    paid_at = milestone_date(request.form, "paid", existing["paid_at"], today)
    shipped_at = milestone_date(request.form, "shipped", existing["shipped_at"], today)
    received_at = milestone_date(request.form, "received", existing["received_at"], today)

    db.execute(
        """
        UPDATE supplier_orders
        SET label = %s, ordered_at = %s, paid_at = %s, shipped_at = %s, received_at = %s
        WHERE id = %s
        """,
        (label or None, ordered_at, paid_at, shipped_at, received_at, supplier_order_id),
    )
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/supplier_orders/delete/<int:supplier_order_id>", methods=["POST"])
def delete_supplier_order(supplier_order_id):
    db = get_db()
    db.execute("UPDATE orders SET supplier_order_id = NULL WHERE supplier_order_id = %s", (supplier_order_id,))
    db.execute("DELETE FROM supplier_orders WHERE id = %s", (supplier_order_id,))
    db.commit()
    return redirect(url_for("admin"))


with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=True)
