"""Zero-dependency HTTP API for Group Expense Splitter."""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import secrets
import sqlite3
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

from logic import (
    ValidationError,
    calculate_balances,
    parse_paise,
    settle_up,
    split_expense,
    validate_date,
)

ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get("EXPENSE_DB", ROOT / "data" / "splitter.db"))
SESSIONS: dict[str, int] = {}


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
        CREATE TABLE IF NOT EXISTS groups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS group_members (
          group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(group_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS expenses (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
          description TEXT NOT NULL,
          amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
          paid_by INTEGER NOT NULL REFERENCES users(id),
          split_type TEXT NOT NULL CHECK(split_type IN ('equal','exact','percentage')),
          expense_date TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS expense_shares (
          expense_id INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id),
          amount_paise INTEGER NOT NULL CHECK(amount_paise >= 0),
          PRIMARY KEY(expense_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS payments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
          payer_id INTEGER NOT NULL REFERENCES users(id),
          payee_id INTEGER NOT NULL REFERENCES users(id),
          amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
          payment_date TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS group_expenses_date ON expenses(group_id, expense_date DESC);
        CREATE INDEX IF NOT EXISTS group_payments_date ON payments(group_id, payment_date DESC);
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


def valid_name(value: object, label: str, maximum: int = 80) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValidationError(f"{label} is required and must be under {maximum} characters")
    return text


def user_json(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


def member_ids(connection: sqlite3.Connection, group_id: int) -> list[int]:
    return [row["user_id"] for row in connection.execute("SELECT user_id FROM group_members WHERE group_id = ? ORDER BY joined_at, user_id", (group_id,))]


def group_for(connection: sqlite3.Connection, user_id: int, group_id: object) -> sqlite3.Row:
    try:
        group_id = int(group_id)
    except (TypeError, ValueError) as error:
        raise ValidationError("Group not found") from error
    group = connection.execute(
        "SELECT g.*, u.name AS creator_name FROM groups g JOIN group_members gm ON gm.group_id = g.id JOIN users u ON u.id = g.created_by WHERE g.id = ? AND gm.user_id = ?",
        (group_id, user_id),
    ).fetchone()
    if group is None:
        raise ValidationError("Group not found")
    return group


def member_for(connection: sqlite3.Connection, group_id: int, user_id: object) -> sqlite3.Row:
    try:
        parsed_id = int(user_id)
    except (TypeError, ValueError) as error:
        raise ValidationError("Choose a group member") from error
    row = connection.execute(
        "SELECT u.id, u.name, u.email FROM users u JOIN group_members gm ON gm.user_id = u.id WHERE gm.group_id = ? AND u.id = ?",
        (group_id, parsed_id),
    ).fetchone()
    if row is None:
        raise ValidationError("That person is not in this group")
    return row


def compute_balances(connection: sqlite3.Connection, group_id: int) -> dict[int, int]:
    ids = member_ids(connection, group_id)
    expenses: list[tuple[int, int, dict[int, int]]] = []
    for row in connection.execute("SELECT id, paid_by, amount_paise FROM expenses WHERE group_id = ?", (group_id,)):
        shares = {share["user_id"]: share["amount_paise"] for share in connection.execute("SELECT user_id, amount_paise FROM expense_shares WHERE expense_id = ?", (row["id"],))}
        expenses.append((row["paid_by"], row["amount_paise"], shares))
    payments = [(row["payer_id"], row["payee_id"], row["amount_paise"]) for row in connection.execute("SELECT payer_id, payee_id, amount_paise FROM payments WHERE group_id = ?", (group_id,))]
    return calculate_balances(ids, expenses, payments)


def member_payloads(connection: sqlite3.Connection, group_id: int, balances: dict[int, int]) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT u.id, u.name, u.email FROM users u JOIN group_members gm ON gm.user_id = u.id WHERE gm.group_id = ? ORDER BY gm.joined_at, u.name",
        (group_id,),
    )
    return [{**dict(row), "net_paise": balances.get(row["id"], 0)} for row in rows]


def expense_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    shares = []
    for share in connection.execute(
        "SELECT es.user_id, es.amount_paise, u.name, u.email FROM expense_shares es JOIN users u ON u.id = es.user_id WHERE es.expense_id = ? ORDER BY u.name",
        (row["id"],),
    ):
        shares.append(dict(share))
    return {**dict(row), "shares": shares}


def group_payload(connection: sqlite3.Connection, group: sqlite3.Row) -> dict[str, Any]:
    balances = compute_balances(connection, group["id"])
    expenses = [
        expense_payload(connection, row)
        for row in connection.execute(
            "SELECT e.*, u.name AS paid_by_name FROM expenses e JOIN users u ON u.id = e.paid_by WHERE e.group_id = ? ORDER BY e.expense_date DESC, e.id DESC",
            (group["id"],),
        )
    ]
    payments = [
        dict(row)
        for row in connection.execute(
            "SELECT p.*, payer.name AS payer_name, payee.name AS payee_name FROM payments p JOIN users payer ON payer.id = p.payer_id JOIN users payee ON payee.id = p.payee_id WHERE p.group_id = ? ORDER BY p.payment_date DESC, p.id DESC",
            (group["id"],),
        )
    ]
    plan = settle_up(balances)
    names = {row["id"]: row["name"] for row in connection.execute("SELECT id, name FROM users WHERE id IN ({})".format(",".join("?" * len(balances))), tuple(balances))} if balances else {}
    plan = [{**item, "from_name": names.get(item["from_user_id"], "Member"), "to_name": names.get(item["to_user_id"], "Member")} for item in plan]
    return {
        "id": group["id"], "name": group["name"], "created_by": group["created_by"], "creator_name": group["creator_name"],
        "members": member_payloads(connection, group["id"], balances), "expenses": expenses, "payments": payments,
        "settlement_plan": plan, "total_expenses_paise": sum(item["amount_paise"] for item in expenses),
    }


def create_expense(connection: sqlite3.Connection, user_id: int, group_id: int, payload: dict[str, Any], expense_id: int | None = None) -> int:
    group_for(connection, user_id, group_id)
    description = valid_name(payload.get("description"), "Description", 100)
    amount = parse_paise(payload.get("amount"))
    date_value = validate_date(payload.get("date"))
    paid_by = member_for(connection, group_id, payload.get("paid_by"))["id"]
    ids = member_ids(connection, group_id)
    split_type = str(payload.get("split_type") or "equal").strip().lower()
    shares = split_expense(amount, ids, split_type, payload.get("split_values"))
    if expense_id is None:
        cursor = connection.execute(
            "INSERT INTO expenses(group_id, description, amount_paise, paid_by, split_type, expense_date) VALUES (?, ?, ?, ?, ?, ?)",
            (group_id, description, amount, paid_by, split_type, date_value),
        )
        expense_id = int(cursor.lastrowid)
    else:
        existing = connection.execute("SELECT id FROM expenses WHERE id = ? AND group_id = ?", (expense_id, group_id)).fetchone()
        if existing is None:
            raise ValidationError("Expense not found")
        connection.execute(
            "UPDATE expenses SET description=?, amount_paise=?, paid_by=?, split_type=?, expense_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND group_id=?",
            (description, amount, paid_by, split_type, date_value, expense_id, group_id),
        )
        connection.execute("DELETE FROM expense_shares WHERE expense_id = ?", (expense_id,))
    connection.executemany("INSERT INTO expense_shares(expense_id, user_id, amount_paise) VALUES (?, ?, ?)", [(expense_id, member_id, share) for member_id, share in shares.items()])
    return expense_id


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "GroupExpenseSplitter/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def json_response(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        if getattr(self, "_session_token", None):
            self.send_header("Set-Cookie", f"splitter_session={self._session_token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800")
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
            value = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as error:
            raise ValidationError("Send valid JSON") from error
        if not isinstance(value, dict):
            raise ValidationError("Request body must be an object")
        return value

    def current_user(self) -> sqlite3.Row | None:
        cookies = {}
        for item in self.headers.get("Cookie", "").split(";"):
            if "=" in item:
                key, value = item.strip().split("=", 1)
                cookies[key] = value
        user_id = SESSIONS.get(cookies.get("splitter_session", ""))
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

    def route(self) -> tuple[str, list[str], dict[str, str]]:
        parsed = urllib.parse.urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        return parsed.path, parts, query

    def do_GET(self) -> None:
        path, parts, query = self.route()
        try:
            if path.startswith("/api/"):
                self.api_get(parts, query)
            else:
                self.static(path)
        except ValidationError as error:
            self.error(str(error), 401 if str(error).startswith("Please") else 400)
        except Exception as error:
            print(f"GET {path}: {error}")
            self.error("Something went wrong on the server", 500)

    def do_POST(self) -> None:
        path, parts, _ = self.route()
        try:
            self.api_post(parts)
        except ValidationError as error:
            self.error(str(error), 401 if str(error).startswith("Please") else 400)
        except sqlite3.IntegrityError as error:
            message = "That email or group membership already exists" if "UNIQUE" in str(error).upper() else "The data could not be saved"
            self.error(message, 409)
        except (ValueError, TypeError):
            self.error("Enter valid values", 400)
        except Exception as error:
            print(f"POST {path}: {error}")
            self.error("Something went wrong on the server", 500)

    def do_DELETE(self) -> None:
        path, parts, _ = self.route()
        try:
            user = self.require_user()
            connection = connect()
            try:
                if len(parts) == 4 and parts[:2] == ["api", "groups"] and parts[3] == "expenses":
                    group = group_for(connection, user["id"], parts[2])
                    raise ValidationError("Expense not found")
                if len(parts) == 5 and parts[:2] == ["api", "groups"] and parts[3] == "expenses":
                    group = group_for(connection, user["id"], parts[2])
                    try:
                        expense_id = int(parts[4])
                    except ValueError as error:
                        raise ValidationError("Expense not found") from error
                    cursor = connection.execute("DELETE FROM expenses WHERE id = ? AND group_id = ?", (expense_id, group["id"]))
                    if cursor.rowcount == 0:
                        raise ValidationError("Expense not found")
                    connection.commit()
                    self.json_response({"ok": True})
                    return
                self.error("Not found", 404)
            finally:
                connection.close()
        except ValidationError as error:
            self.error(str(error), 401 if str(error).startswith("Please") else 400)

    def api_get(self, parts: list[str], query: dict[str, str]) -> None:
        user = self.require_user()
        connection = connect()
        try:
            if parts == ["api", "me"]:
                self.json_response({"user": user_json(user)})
            elif parts == ["api", "groups"]:
                rows = connection.execute(
                    "SELECT g.id, g.name, g.created_by, g.created_at, (SELECT COUNT(*) FROM group_members gm2 WHERE gm2.group_id=g.id) AS member_count FROM groups g JOIN group_members gm ON gm.group_id=g.id WHERE gm.user_id=? ORDER BY g.created_at DESC, g.id DESC",
                    (user["id"],),
                )
                self.json_response({"groups": [dict(row) for row in rows]})
            elif len(parts) == 3 and parts[:2] == ["api", "groups"]:
                group = group_for(connection, user["id"], parts[2])
                self.json_response({"group": group_payload(connection, group)})
            else:
                self.error("Not found", 404)
        finally:
            connection.close()

    def api_post(self, parts: list[str]) -> None:
        payload = self.body()
        if parts == ["api", "register"]:
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
                connection.commit()
                self.start_session(cursor.lastrowid)
                self.json_response({"user": {"id": cursor.lastrowid, "name": name, "email": email}}, 201)
            finally:
                connection.close()
            return
        if parts == ["api", "login"]:
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
        if parts == ["api", "logout"]:
            cookie = self.headers.get("Cookie", "")
            token = next((item.strip().split("=", 1)[1] for item in cookie.split(";") if item.strip().startswith("splitter_session=")), "")
            SESSIONS.pop(token, None)
            self.send_response(204)
            self.end_headers()
            return

        user = self.require_user()
        connection = connect()
        try:
            if parts == ["api", "groups"]:
                name = valid_name(payload.get("name"), "Group name", 80)
                cursor = connection.execute("INSERT INTO groups(name, created_by) VALUES (?, ?)", (name, user["id"]))
                group_id = cursor.lastrowid
                connection.execute("INSERT INTO group_members(group_id, user_id) VALUES (?, ?)", (group_id, user["id"]))
                connection.commit()
                self.json_response({"group_id": group_id}, 201)
                return
            if len(parts) == 4 and parts[:2] == ["api", "groups"] and parts[3] == "members":
                group = group_for(connection, user["id"], parts[2])
                email = str(payload.get("email", "")).strip().lower()
                if "@" not in email:
                    raise ValidationError("Enter the registered member's email")
                member = connection.execute("SELECT id, name, email FROM users WHERE email = ?", (email,)).fetchone()
                if member is None:
                    raise ValidationError("No registered user has that email")
                connection.execute("INSERT INTO group_members(group_id, user_id) VALUES (?, ?)", (group["id"], member["id"]))
                connection.commit()
                self.json_response({"member": dict(member)}, 201)
                return
            if len(parts) == 4 and parts[:2] == ["api", "groups"] and parts[3] == "expenses":
                group = group_for(connection, user["id"], parts[2])
                expense_id = create_expense(connection, user["id"], group["id"], payload)
                connection.commit()
                self.json_response({"expense_id": expense_id}, 201)
                return
            if len(parts) == 6 and parts[:2] == ["api", "groups"] and parts[3] == "expenses" and parts[5] == "edit":
                group = group_for(connection, user["id"], parts[2])
                try:
                    expense_id = int(parts[4])
                except (TypeError, ValueError) as error:
                    raise ValidationError("Expense not found") from error
                create_expense(connection, user["id"], group["id"], payload, expense_id)
                connection.commit()
                self.json_response({"ok": True})
                return
            if len(parts) == 4 and parts[:2] == ["api", "groups"] and parts[3] == "payments":
                group = group_for(connection, user["id"], parts[2])
                payer = member_for(connection, group["id"], payload.get("payer_id"))["id"]
                payee = member_for(connection, group["id"], payload.get("payee_id"))["id"]
                if payer == payee:
                    raise ValidationError("Payer and recipient must be different")
                amount = parse_paise(payload.get("amount"))
                payment_date = validate_date(payload.get("date") or date.today().isoformat())
                note = str(payload.get("note", "")).strip()
                if len(note) > 160:
                    raise ValidationError("Payment note must be under 160 characters")
                cursor = connection.execute(
                    "INSERT INTO payments(group_id, payer_id, payee_id, amount_paise, payment_date, note) VALUES (?, ?, ?, ?, ?, ?)",
                    (group["id"], payer, payee, amount, payment_date, note),
                )
                connection.commit()
                self.json_response({"payment_id": cursor.lastrowid}, 201)
                return
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
    print(f"Group Expense Splitter is running at http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
