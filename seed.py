"""Create demo data so the tracker is ready to explore after one command."""

from datetime import date, timedelta

from server import connect, ensure_categories, init_db, password_hash, transaction_input


def seed() -> None:
    init_db()
    connection = connect()
    try:
        user = connection.execute("SELECT * FROM users WHERE email = 'maya@demo.com'").fetchone()
        if user is None:
            cursor = connection.execute("INSERT INTO users(name, email, password_hash) VALUES (?, ?, ?)", ("Maya Kapoor", "maya@demo.com", password_hash("demo123")))
            user_id = cursor.lastrowid
            ensure_categories(connection, user_id)
        else:
            user_id = user["id"]
        accounts = {}
        for name, account_type, opening in [("HDFC Salary", "bank", "18000"), ("Everyday cash", "cash", "1200"), ("Travel card", "card", "0")]:
            row = connection.execute("SELECT * FROM accounts WHERE user_id = ? AND name = ?", (user_id, name)).fetchone()
            if row is None:
                cursor = connection.execute("INSERT INTO accounts(user_id, name, type, opening_balance_paise) VALUES (?, ?, ?, ?)", (user_id, name, account_type, int(float(opening) * 100)))
                accounts[name] = cursor.lastrowid
            else:
                accounts[name] = row["id"]
        if connection.execute("SELECT 1 FROM transactions WHERE user_id = ? LIMIT 1", (user_id,)).fetchone() is None:
            today = date.today()
            demo_rows = [
                {"kind": "income", "amount": "82000", "date": today.replace(day=1).isoformat(), "account_id": accounts["HDFC Salary"], "category": "Salary", "note": "Monthly salary"},
                {"kind": "expense", "amount": "18500", "date": today.replace(day=3).isoformat(), "account_id": accounts["HDFC Salary"], "category": "Housing", "note": "Rent"},
                {"kind": "expense", "amount": "2460", "date": (today - timedelta(days=2)).isoformat(), "account_id": accounts["Everyday cash"], "category": "Food", "note": "Groceries and lunch"},
                {"kind": "expense", "amount": "1100", "date": (today - timedelta(days=5)).isoformat(), "account_id": accounts["Travel card"], "category": "Transport", "note": "Metro and rides"},
                {"kind": "transfer", "amount": "8000", "date": (today - timedelta(days=8)).isoformat(), "account_id": accounts["HDFC Salary"], "transfer_account_id": accounts["Everyday cash"], "note": "Cash for the month"},
                {"kind": "expense", "amount": "4200", "date": (today - timedelta(days=12)).isoformat(), "account_id": accounts["Travel card"], "category": "Shopping", "note": "Home supplies"},
            ]
            for item in demo_rows:
                transaction_input(connection, user_id, item)
        month = date.today().strftime("%Y-%m")
        for category, limit in [("Housing", "20000"), ("Food", "9000"), ("Transport", "4000"), ("Shopping", "6000")]:
            category_row = connection.execute("SELECT id FROM categories WHERE user_id = ? AND name = ?", (user_id, category)).fetchone()
            connection.execute("INSERT OR IGNORE INTO budgets(user_id, category_id, month, limit_paise) VALUES (?, ?, ?, ?)", (user_id, category_row["id"], month, int(float(limit) * 100)))
        connection.commit()
        print("Seeded Ledgerly demo. Login: maya@demo.com / demo123")
    finally:
        connection.close()


if __name__ == "__main__":
    seed()
