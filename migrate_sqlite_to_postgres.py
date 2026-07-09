import os
import sqlite3

import psycopg

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "orders.db")


def main():
    database_url = os.environ["DATABASE_URL"]
    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row
    pconn = psycopg.connect(database_url)

    catalog_count = 0
    for row in sconn.execute("SELECT * FROM catalog_items"):
        pconn.execute(
            """
            INSERT INTO catalog_items (name, size, price, available, created_at, image_url)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (row["name"], row["size"], row["price"], row["available"], row["created_at"], row["image_url"]),
        )
        catalog_count += 1
        if row["image_url"] and not row["image_url"].startswith("http"):
            print(f"  NOTE: '{row['name']}' has a local image file that won't carry over — re-upload it after migrating.")

    supplier_id_map = {}
    for row in sconn.execute("SELECT * FROM supplier_orders"):
        cur = pconn.execute(
            """
            INSERT INTO supplier_orders (label, ordered_at, paid_at, shipped_at, received_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (row["label"], row["ordered_at"], row["paid_at"], row["shipped_at"], row["received_at"], row["created_at"]),
        )
        supplier_id_map[row["id"]] = cur.fetchone()[0]

    order_count = 0
    for row in sconn.execute("SELECT * FROM orders"):
        new_supplier_id = supplier_id_map.get(row["supplier_order_id"]) if row["supplier_order_id"] else None
        pconn.execute(
            """
            INSERT INTO orders
                (coworker_name, item_description, size, quantity, notes, status, price, cost, created_at, request_id, supplier_order_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["coworker_name"],
                row["item_description"],
                row["size"],
                row["quantity"],
                row["notes"],
                row["status"],
                row["price"],
                row["cost"],
                row["created_at"],
                row["request_id"],
                new_supplier_id,
            ),
        )
        order_count += 1

    custom_count = 0
    for row in sconn.execute("SELECT * FROM custom_requests"):
        pconn.execute(
            """
            INSERT INTO custom_requests (request_id, coworker_name, description, admin_reply, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (row["request_id"], row["coworker_name"], row["description"], row["admin_reply"], row["status"], row["created_at"]),
        )
        custom_count += 1

    pconn.commit()
    print("Migration complete:")
    print(f"  catalog_items: {catalog_count}")
    print(f"  supplier_orders: {len(supplier_id_map)}")
    print(f"  orders: {order_count}")
    print(f"  custom_requests: {custom_count}")


if __name__ == "__main__":
    main()
