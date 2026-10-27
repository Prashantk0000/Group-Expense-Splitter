# Splitwise — Group Expense Splitter

A full-stack group expense app for creating groups, adding registered members, recording shared expenses, and settling balances with as few payments as possible.

## Run locally

Requirements: Python 3.10+. The app uses only the Python standard library, SQLite, and browser APIs.

```bash
python seed.py
python server.py
```

Open <http://127.0.0.1:8000>.

PowerShell configuration:

```powershell
$env:EXPENSE_DB=".\data\local.db"
$env:PORT="8001"
python server.py
```

### Demo logins

All demo users use password `demo123`:

| Name | Email |
| --- | --- |
| Maya Kapoor | maya@demo.com |
| Arjun Mehta | arjun@demo.com |
| Zoya Khan | zoya@demo.com |

The seed creates Maya's **Goa Weekend** group with two expenses. It is safe to run again.

## Data model and design decisions

SQLite stores every amount as integer paise. The main tables are:

- `users`: registered accounts used for group invitations.
- `groups` and `group_members`: group ownership and membership.
- `expenses` and `expense_shares`: the payer, total, split mode, and one exact share per member.
- `payments`: recorded settlements between two group members.

Equal splits use integer division and distribute leftover paise deterministically. Exact splits must sum to the total. Percentage splits must sum to 100%; they round down each share and assign the remaining paise to the final member. This guarantees that shares always equal the expense total.

A member's net balance is `what they paid + payments received - their expense shares - payments made`. Positive balances are owed money; negative balances owe money. Settlement uses a depth-first cancellation search for groups up to 12 members to minimize payment count, with a correct greedy fallback for unusually large groups.

The backend is a small standard-library `http.server` JSON API. Every write is validated server-side, and API errors are shown in the UI. The responsive frontend works on phone and laptop layouts.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover paise parsing, equal split remainders, exact and percentage validation, balance calculations, recorded payments, date validation, and minimum-payment settlement behavior.

## Finished and next steps

Finished: login and registration, group creation, registered-user invitations, equal/exact/percentage expenses, edit/delete, per-member net balances, minimum-payment settle-up plans, recorded payments, seed demo data, automated tests, and responsive UI.

With more time I would add activity-feed entries, push/email reminders for unsettled payments, and deployment configuration with HTTPS.

## AI tools

OpenCode (GPT-6 Astra) was used to implement the full-stack application, including the SQLite data model, settlement algorithm, validation, tests, seed data, and responsive UI. The accounting rules and resulting behavior were reviewed manually.

## Environment

Copy `.env.example` when customizing local settings. Never commit secrets.
