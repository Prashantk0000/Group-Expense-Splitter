"""Zero-dependency backend for the Personal Budget Tracker."""

from __future__ import annotations

import csv
import hashlib
import http.server
import io
import json
import os
import secrets
import sqlite3
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Any

from logic import (
    LedgerEntry,
    ValidationError,
    account_balances,
    budget_progress,
    parse_paise,
    validate_date,
    validate_month,
    validate_transaction_kind,
)


ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get("BUDGET_DB", ROOT / "data" / "budget.db"))
SESSIONS: dict[str, int] = {}
ACCOUNT_TYPES = {"cash", "bank", "card"}
DEFAULT_CATEGORIES = ["Housing", "Food", "Transport", "Bills", "Shopping", "Health", "Entertainment", "Other"]


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or connect()
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          email TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          type TEXT NOT NULL CHECK(type IN ('cash','bank','card')),
          opening_balance_paise INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(user_id, name)
        );
        CREATE TABLE IF NOT EXISTS categories (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          UNIQUE(user_id, name COLLATE NOCASE)
        );
        CREATE TABLE IF NOT EXISTS transactions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK(kind IN ('income','expense','transfer')),
          amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
          date TEXT NOT NULL,
          category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
          account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
          transfer_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
          note TEXT NOT NULL DEFAULT '',
          import_hash TEXT UNIQUE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS budgets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
          month TEXT NOT NULL,
          limit_paise INTEGER NOT NULL CHECK(limit_paise > 0),
          UNIQUE(user_id, category_id, month)
        );
        CREATE INDEX IF NOT EXISTS transactions_user_date ON transactions(user_id, date);
        """
    )
    connection.commit()
    if owns_connection:
        connection.close()


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"{salt.hex()}${digest.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        salt, digest = encoded.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000)
        return secrets.compare_digest(actual.hex(), digest)
    except (ValueError, TypeError):
        return False


def money(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def user_json(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


def valid_name(value: object, label: str, maximum: int = 80) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValidationError(f"{label} is required and must be under {maximum} characters")
    return text


def get_category(connection: sqlite3.Connection, user_id: int, name: object, *, create: bool = True) -> sqlite3.Row:
    category_name = valid_name(name, "Category", 50)
    row = connection.execute("SELECT * FROM categories WHERE user_id = ? AND name = ? COLLATE NOCASE", (user_id, category_name)).fetchone()
    if row:
        return row
    if not create:
        raise ValidationError(f"Category '{category_name}' was not found")
    cursor = connection.execute("INSERT INTO categories(user_id, name) VALUES (?, ?)", (user_id, category_name))
    return connection.execute("SELECT * FROM categories WHERE id = ?", (cursor.lastrowid,)).fetchone()


def ensure_categories(connection: sqlite3.Connection, user_id: int) -> None:
    for category in DEFAULT_CATEGORIES:
        connection.execute("INSERT OR IGNORE INTO categories(user_id, name) VALUES (?, ?)", (user_id, category))


def account_for(connection: sqlite3.Connection, user_id: int, account_id: object) -> sqlite3.Row:
    try:
        account_id = int(account_id)
    except (TypeError, ValueError) as error:
        raise ValidationError("Choose an account") from error
    account = connection.execute("SELECT * FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id)).fetchone()
    if account is None:
        raise ValidationError("Account not found")
    return account


def entries_for(connection: sqlite3.Connection, user_id: int) -> list[LedgerEntry]:
    return [LedgerEntry(row["kind"], row["amount_paise"], row["account_id"], row["transfer_account_id"])
            for row in connection.execute("SELECT kind, amount_paise, account_id, transfer_account_id FROM transactions WHERE user_id = ?", (user_id,))]


def account_payloads(connection: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    rows = list(connection.execute("SELECT * FROM accounts WHERE user_id = ? ORDER BY created_at, id", (user_id,)))
    balances = account_balances((row["id"] for row in rows), entries_for(connection, user_id))
    return [{**dict(row), "balance_paise": balances[row["id"]]} for row in rows]


def transaction_payload(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def transaction_rows(connection: sqlite3.Connection, user_id: int, month: str | None = None) -> list[dict[str, Any]]:
    query = ("SELECT t.*, a.name AS account_name, ta.name AS transfer_account_name, c.name AS category_name "
             "FROM transactions t JOIN accounts a ON a.id = t.account_id "
             "LEFT JOIN accounts ta ON ta.id = t.transfer_account_id LEFT JOIN categories c ON c.id = t.category_id "
             "WHERE t.user_id = ?")
    params: list[Any] = [user_id]
    if month:
        query += " AND substr(t.date, 1, 7) = ?"
        params.append(month)
    query += " ORDER BY t.date DESC, t.id DESC"
    return [transaction_payload(row) for row in connection.execute(query, params)]


def month_totals(rows: list[dict[str, Any]], month: str) -> tuple[int, int]:
    income = sum(row["amount_paise"] for row in rows if row["kind"] == "income" and row["date"].startswith(month))
    expense = sum(row["amount_paise"] for row in rows if row["kind"] == "expense" and row["date"].startswith(month))
    return income, expense


def previous_months(month: str, count: int = 6) -> list[str]:
    year, current = (int(part) for part in month.split("-"))
    result = []
    for _ in range(count):
        result.append(f"{year:04d}-{current:02d}")
        current -= 1
        if current == 0:
            current, year = 12, year - 1
    return list(reversed(result))


def serialize_dashboard(connection: sqlite3.Connection, user_id: int, month: str) -> dict[str, Any]:
    all_transactions = transaction_rows(connection, user_id)
    selected = [row for row in all_transactions if row["date"].startswith(month)]
    income, expense = month_totals(all_transactions, month)
    categories = []
    spending: dict[str, int] = {}
    for row in selected:
        if row["kind"] == "expense":
            name = row["category_name"] or "Other"
            spending[name] = spending.get(name, 0) + row["amount_paise"]
    for name, amount in sorted(spending.items(), key=lambda item: item[1], reverse=True):
        categories.append({"name": name, "amount_paise": amount})
    six_months = []
    for item in previous_months(month):
        item_income, item_expense = month_totals(all_transactions, item)
        six_months.append({"month": item, "income_paise": item_income, "expense_paise": item_expense})
    budgets = []
    for row in connection.execute(
        "SELECT b.*, c.name FROM budgets b JOIN categories c ON c.id = b.category_id WHERE b.user_id = ? AND b.month = ? ORDER BY c.name",
        (user_id, month),
    ):
        spent = spending.get(row["name"], 0)
        budgets.append({"id": row["id"], "category_id": row["category_id"], "category_name": row["name"], **budget_progress(row["limit_paise"], spent)})
    return {
        "month": month,
        "income_paise": income,
        "expense_paise": expense,
        "net_paise": income - expense,
        "category_spending": categories,
        "six_months": six_months,
        "budgets": budgets,
        "transactions": selected,
    }


def transaction_input(connection: sqlite3.Connection, user_id: int, payload: dict[str, Any], transaction_id: int | None = None, import_hash: str | None = None) -> int:
    kind = validate_transaction_kind(payload.get("kind"))
    amount = parse_paise(payload.get("amount"))
    transaction_date = validate_date(payload.get("date"))
    account = account_for(connection, user_id, payload.get("account_id"))
    transfer_account_id = None
    category_id = None
    if kind == "transfer":
        transfer = account_for(connection, user_id, payload.get("transfer_account_id"))
        if transfer["id"] == account["id"]:
            raise ValidationError("Transfer accounts must be different")
        transfer_account_id = transfer["id"]
    else:
        if kind == "expense" and not str(payload.get("category") or "").strip():
            raise ValidationError("Choose a category for this expense")
        if str(payload.get("category") or "").strip():
            category_id = get_category(connection, user_id, payload["category"])["id"]
    note = str(payload.get("note", "")).strip()
    if len(note) > 240:
        raise ValidationError("Note must be under 240 characters")
    fields = (kind, amount, transaction_date, category_id, account["id"], transfer_account_id, note, import_hash)
    if transaction_id is None:
        cursor = connection.execute(
            "INSERT INTO transactions(user_id, kind, amount_paise, date, category_id, account_id, transfer_account_id, note, import_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, *fields),
        )
        return int(cursor.lastrowid)
    existing = connection.execute("SELECT id FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id)).fetchone()
    if existing is None:
        raise ValidationError("Transaction not found")
    connection.execute(
        "UPDATE transactions SET kind=?, amount_paise=?, date=?, category_id=?, account_id=?, transfer_account_id=?, note=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
        (*fields[:-1], transaction_id, user_id),
    )
    return transaction_id


def csv_rows(text: str) -> tuple[list[str], list[dict[str, str]]]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 5_000_000:
        raise ValidationError("CSV file must be under 5 MB")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    headers = [header.strip() for header in (reader.fieldnames or []) if header]
    if not headers:
        raise ValidationError("CSV needs a header row")
    rows = []
    for row in reader:
        rows.append({str(key).strip(): str(value or "").strip() for key, value in row.items() if key})
    return headers, rows


def mapping_value(row: dict[str, str], mapping: dict[str, Any], key: str) -> str:
    column = str(mapping.get(key) or "")
    return row.get(column, "").strip() if column else ""


def import_preview(connection: sqlite3.Connection, user_id: int, text: str, mapping: dict[str, Any]) -> dict[str, Any]:
    headers, raw_rows = csv_rows(text)
    accounts = {row["name"].lower(): row["id"] for row in connection.execute("SELECT id, name FROM accounts WHERE user_id = ?", (user_id,))}
    result = []
    for index, row in enumerate(raw_rows, 2):
        values = {
            "date": mapping_value(row, mapping, "date"), "amount": mapping_value(row, mapping, "amount"),
            "category": mapping_value(row, mapping, "category"), "account": mapping_value(row, mapping, "account"),
            "note": mapping_value(row, mapping, "note"), "kind": mapping_value(row, mapping, "kind") or "expense",
        }
        errors: list[str] = []
        try:
            values["date"] = validate_date(values["date"])
        except ValidationError as error:
            errors.append(str(error))
        try:
            values["amount_paise"] = parse_paise(values["amount"])
        except ValidationError as error:
            errors.append(str(error))
        try:
            values["kind"] = validate_transaction_kind(values["kind"])
        except ValidationError as error:
            errors.append(str(error))
        account_id = accounts.get(values["account"].lower())
        if not account_id:
            errors.append(f"Account '{values['account'] or 'blank'}' was not found")
        if values["kind"] == "expense" and not values["category"]:
            errors.append("Category is required for expenses")
        if values["kind"] == "transfer":
            errors.append("Transfers need a second account and should be added manually")
        fingerprint = hashlib.sha256(json.dumps([user_id, values["date"], values.get("amount_paise"), values["kind"], values["category"].lower(), account_id, values["note"]], sort_keys=True).encode()).hexdigest()
        duplicate = connection.execute("SELECT 1 FROM transactions WHERE import_hash = ?", (fingerprint,)).fetchone() is not None
        if duplicate:
            errors.append("Already imported")
        result.append({"row_number": index, **values, "account_id": account_id, "import_hash": fingerprint, "errors": errors, "valid": not errors})
    return {"headers": headers, "rows": result, "valid_count": sum(row["valid"] for row in result), "error_count": sum(not row["valid"] for row in result)}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BudgetTracker/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def json_response(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        if getattr(self, "_session_token", None):
            self.send_header("Set-Cookie", f"budget_session={self._session_token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def error(self, message: str, status: int = 400) -> None:
        self.json_response({"error": message}, status)

    def body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValidationError("Invalid request") from error
        if length > 6_000_000:
            raise ValidationError("Request is too large")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            raise ValidationError("Send valid JSON") from error
        if not isinstance(value, dict):
            raise ValidationError("Request body must be an object")
        return value

    def current_user(self) -> sqlite3.Row | None:
        token = self.headers.get("Cookie", "").replace("budget_session=", "").split(";", 1)[0]
        user_id = SESSIONS.get(token)
        if not user_id:
            return None
        connection = connect()
        try:
            return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            connection.close()

    def require_user(self) -> sqlite3.Row:
        user = self.current_user()
        if user is None:
            raise ValidationError("Please log in to continue")
        return user

    def route(self) -> tuple[str, dict[str, str]]:
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}

    def do_GET(self) -> None:
        path, query = self.route()
        try:
            if path.startswith("/api/"):
                self.api_get(path, query)
            else:
                self.static(path)
        except ValidationError as error:
            self.error(str(error), 401 if str(error).startswith("Please") else 400)
        except Exception as error:
            print(f"GET {path}: {error}")
            self.error("Something went wrong on the server", 500)

    def do_POST(self) -> None:
        path, _ = self.route()
        try:
            self.api_post(path)
        except ValidationError as error:
            self.error(str(error), 401 if str(error).startswith("Please") else 400)
        except sqlite3.IntegrityError as error:
            message = "That value already exists" if "UNIQUE" in str(error).upper() else "The data could not be saved"
            self.error(message, 409)
        except (ValueError, TypeError):
            self.error("Enter valid values", 400)
        except Exception as error:
            print(f"POST {path}: {error}")
            self.error("Something went wrong on the server", 500)

    def do_DELETE(self) -> None:
        path, _ = self.route()
        connection = None
        try:
            user = self.require_user()
            connection = connect()
            if path.startswith("/api/transactions/"):
                try:
                    transaction_id = int(path.rsplit("/", 1)[1])
                except ValueError as error:
                    raise ValidationError("Transaction not found") from error
                cursor = connection.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user["id"]))
                if cursor.rowcount == 0:
                    raise ValidationError("Transaction not found")
                connection.commit()
                self.json_response({"ok": True})
                return
            self.error("Not found", 404)
        except ValidationError as error:
            self.error(str(error), 401 if str(error).startswith("Please") else 400)
        finally:
            if connection:
                connection.close()

    def api_get(self, path: str, query: dict[str, str]) -> None:
        user = self.require_user()
        connection = connect()
        try:
            if path == "/api/me":
                self.json_response({"user": user_json(user)})
            elif path == "/api/accounts":
                self.json_response({"accounts": account_payloads(connection, user["id"])})
            elif path == "/api/categories":
                ensure_categories(connection, user["id"])
                connection.commit()
                self.json_response({"categories": [dict(row) for row in connection.execute("SELECT * FROM categories WHERE user_id = ? ORDER BY name", (user["id"],))]})
            elif path == "/api/transactions":
                requested_month = validate_month(query["month"]) if query.get("month") else None
                self.json_response({"transactions": transaction_rows(connection, user["id"], requested_month)})
            elif path == "/api/dashboard":
                month = validate_month(query.get("month") or date.today().strftime("%Y-%m"))
                self.json_response({"dashboard": serialize_dashboard(connection, user["id"], month)})
            elif path == "/api/export":
                month = validate_month(query.get("month") or date.today().strftime("%Y-%m"))
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["date", "type", "amount", "category", "account", "transfer_account", "note"])
                for row in transaction_rows(connection, user["id"], month):
                    writer.writerow([row["date"], row["kind"], f"{row['amount_paise'] / 100:.2f}", row["category_name"] or "", row["account_name"], row["transfer_account_name"] or "", row["note"]])
                raw = output.getvalue().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="budget-{month}.csv"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif path == "/api/budgets":
                month = validate_month(query.get("month") or date.today().strftime("%Y-%m"))
                dashboard = serialize_dashboard(connection, user["id"], month)
                self.json_response({"budgets": dashboard["budgets"], "month": month})
            else:
                self.error("Not found", 404)
        finally:
            connection.close()

    def api_post(self, path: str) -> None:
        payload = self.body()
        if path == "/api/register":
            name = valid_name(payload.get("name"), "Name", 60)
            email = str(payload.get("email", "")).strip().lower()
            password = str(payload.get("password", ""))
            if "@" not in email or len(email) > 160:
                raise ValidationError("Enter a valid email")
            if len(password) < 6:
                raise ValidationError("Password must be at least 6 characters")
            connection = connect()
            try:
                cursor = connection.execute("INSERT INTO users(name, email, password_hash) VALUES (?, ?, ?)", (name, email, password_hash(password)))
                ensure_categories(connection, cursor.lastrowid)
                connection.commit()
                self.start_session(cursor.lastrowid)
                self.json_response({"user": {"id": cursor.lastrowid, "name": name, "email": email}}, 201)
            finally:
                connection.close()
            return
        if path == "/api/login":
            email = str(payload.get("email", "")).strip().lower()
            connection = connect()
            try:
                row = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            finally:
                connection.close()
            if row is None or not password_matches(str(payload.get("password", "")), row["password_hash"]):
                raise ValidationError("Email or password is incorrect")
            self.start_session(row["id"])
            self.json_response({"user": user_json(row)})
            return
        if path == "/api/logout":
            token = self.headers.get("Cookie", "").replace("budget_session=", "").split(";", 1)[0]
            SESSIONS.pop(token, None)
            self.send_response(204)
            self.end_headers()
            return

        user = self.require_user()
        connection = connect()
        try:
            if path == "/api/accounts":
                name = valid_name(payload.get("name"), "Account name", 60)
                account_type = str(payload.get("type", "")).strip().lower()
                if account_type not in ACCOUNT_TYPES:
                    raise ValidationError("Choose cash, bank, or card")
                opening = parse_paise(payload.get("opening_balance", 0), allow_zero=True)
                cursor = connection.execute("INSERT INTO accounts(user_id, name, type, opening_balance_paise) VALUES (?, ?, ?, ?)", (user["id"], name, account_type, opening))
                connection.commit()
                self.json_response({"account_id": cursor.lastrowid}, 201)
            elif path == "/api/transactions":
                transaction_id = transaction_input(connection, user["id"], payload)
                connection.commit()
                self.json_response({"transaction_id": transaction_id}, 201)
            elif path.startswith("/api/transactions/") and path.endswith("/edit"):
                transaction_id = int(path.split("/")[3])
                transaction_input(connection, user["id"], payload, transaction_id)
                connection.commit()
                self.json_response({"ok": True})
            elif path == "/api/budgets":
                month = validate_month(payload.get("month"))
                limit = parse_paise(payload.get("limit"))
                category = get_category(connection, user["id"], payload.get("category"))
                connection.execute(
                    "INSERT INTO budgets(user_id, category_id, month, limit_paise) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, category_id, month) DO UPDATE SET limit_paise=excluded.limit_paise",
                    (user["id"], category["id"], month, limit),
                )
                connection.commit()
                self.json_response({"ok": True})
            elif path == "/api/import/inspect":
                headers, rows = csv_rows(str(payload.get("csv", "")))
                self.json_response({"headers": headers, "rows": rows[:100], "total": len(rows)})
            elif path == "/api/import/preview":
                preview = import_preview(connection, user["id"], str(payload.get("csv", "")), payload.get("mapping") or {})
                self.json_response({"preview": preview})
            elif path == "/api/import/commit":
                rows = payload.get("rows")
                if not isinstance(rows, list) or len(rows) > 5000:
                    raise ValidationError("No import rows were supplied")
                imported = skipped = 0
                errors = []
                for item in rows:
                    if not isinstance(item, dict) or item.get("errors"):
                        if isinstance(item, dict):
                            errors.append({"row_number": item.get("row_number"), "errors": item.get("errors") or ["Invalid row"]})
                        continue
                    import_hash = item.get("import_hash")
                    if import_hash and connection.execute("SELECT 1 FROM transactions WHERE import_hash = ?", (import_hash,)).fetchone():
                        skipped += 1
                        continue
                    connection.execute("SAVEPOINT import_row")
                    try:
                        account_for(connection, user["id"], item.get("account_id"))
                        transaction_input(connection, user["id"], {**item, "account_id": item["account_id"], "category": item.get("category")}, import_hash=import_hash)
                        connection.execute("RELEASE SAVEPOINT import_row")
                        imported += 1
                    except sqlite3.IntegrityError:
                        connection.execute("ROLLBACK TO SAVEPOINT import_row")
                        connection.execute("RELEASE SAVEPOINT import_row")
                        if import_hash and connection.execute("SELECT 1 FROM transactions WHERE import_hash = ?", (import_hash,)).fetchone():
                            skipped += 1
                        else:
                            errors.append({"row_number": item.get("row_number"), "errors": ["This row could not be imported"]})
                    except ValidationError as error:
                        connection.execute("ROLLBACK TO SAVEPOINT import_row")
                        connection.execute("RELEASE SAVEPOINT import_row")
                        errors.append({"row_number": item.get("row_number"), "errors": [str(error)]})
                connection.commit()
                self.json_response({"imported": imported, "skipped": skipped, "errors": errors})
            else:
                self.error("Not found", 404)
        finally:
            connection.close()

    def start_session(self, user_id: int) -> None:
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = int(user_id)
        self._session_token = token

    def static(self, path: str) -> None:
        filename = "index.html" if path in {"/", ""} else path.lstrip("/")
        web_root = (ROOT / "web").resolve()
        file_path = (web_root / filename).resolve()
        if web_root not in file_path.parents or not file_path.is_file():
            self.error("Not found", 404)
            return
        content_type = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}.get(file_path.suffix, "application/octet-stream")
        raw = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Budget Tracker is running at http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
