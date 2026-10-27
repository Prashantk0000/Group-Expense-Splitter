"""Create demo users, a group, expenses, and an unfinished settlement plan."""

from datetime import date, timedelta

from server import connect, create_expense, init_db, password_hash

USERS = [
    ("Maya Kapoor", "maya@demo.com", "demo123"),
    ("Arjun Mehta", "arjun@demo.com", "demo123"),
    ("Zoya Khan", "zoya@demo.com", "demo123"),
]


def seed() -> None:
    init_db()
    connection = connect()
    try:
        ids = {}
        for name, email, password in USERS:
            row = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if row is None:
                cursor = connection.execute(
                    "INSERT INTO users(name, email, password_hash) VALUES (?, ?, ?)",
                    (name, email, password_hash(password)),
                )
                ids[email] = cursor.lastrowid
            else:
                ids[email] = row["id"]
        maya, arjun, zoya = (ids[email] for _, email, _ in USERS)
        group = connection.execute("SELECT id FROM groups WHERE name = ? AND created_by = ?", ("Goa Weekend", maya)).fetchone()
        if group is None:
            cursor = connection.execute("INSERT INTO groups(name, created_by) VALUES (?, ?)", ("Goa Weekend", maya))
            group_id = cursor.lastrowid
        else:
            group_id = group["id"]
        for user_id in (maya, arjun, zoya):
            connection.execute("INSERT OR IGNORE INTO group_members(group_id, user_id) VALUES (?, ?)", (group_id, user_id))
        if connection.execute("SELECT 1 FROM expenses WHERE group_id = ? LIMIT 1", (group_id,)).fetchone() is None:
            today = date.today()
            create_expense(connection, maya, group_id, {
                "description": "Villa and dinner",
                "amount": "4200",
                "paid_by": maya,
                "split_type": "equal",
                "split_values": ["", "", ""],
                "date": (today - timedelta(days=2)).isoformat(),
            })
            create_expense(connection, maya, group_id, {
                "description": "Airport cabs",
                "amount": "900",
                "paid_by": arjun,
                "split_type": "equal",
                "split_values": ["", "", ""],
                "date": (today - timedelta(days=1)).isoformat(),
            })
        connection.commit()
        print("Seeded Splitwise-style demo.")
        print("Login: maya@demo.com / demo123")
        print("Other registered members: arjun@demo.com, zoya@demo.com")
    finally:
        connection.close()


if __name__ == "__main__":
    seed()
