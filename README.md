# Ledgerly — Personal Budget Tracker

Ledgerly is a full-stack personal finance app for recording income, expenses, and account-to-account transfers. It shows a monthly overview, category spending, six months of trends, and budget warnings before a category runs out.

## Run locally

Requirements: Python 3.10+. The app uses only the Python standard library, SQLite, and browser APIs.

```bash
python seed.py
python server.py
```

Open <http://127.0.0.1:8000>.

PowerShell configuration:

```powershell
$env:BUDGET_DB=".\data\local.db"
$env:PORT="8001"
python server.py
```

### Demo login

| Email | Password |
| --- | --- |
| maya@demo.com | demo123 |

The seed script creates three accounts, sample transactions, and current-month budgets. It is safe to run again.

## Data model and design decisions

SQLite stores `users`, `accounts`, `categories`, `transactions`, and `budgets`. Money is stored as integer paise to avoid floating-point errors. A transaction has one of three kinds:

- `income`: adds to its account
- `expense`: subtracts from its account and a category budget
- `transfer`: subtracts from the source account and adds to the destination account, without entering income or expense totals

Account balances are derived from the opening balance plus the ledger every time they are requested. This means edits, deletes, and transfers cannot leave a stale balance behind. Imported rows receive a deterministic hash with a database uniqueness constraint, so re-importing the same file skips duplicates.

CSV import is a two-step flow: inspect/map columns, then preview rows. Each row is validated independently and bad dates, missing amounts, unknown accounts, and duplicates are reported without stopping valid rows from being imported.

The backend is a small standard-library `http.server` JSON API. The frontend is a responsive static application in `web/` and works on phone and laptop layouts.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover paise parsing, opening balances, ledger calculations, both sides of transfers, date/month/type validation, and 80%/100% budget warnings.

## Finished and next steps

Finished: authentication, accounts, income/expense/transfer ledger, edits and deletes, monthly budgets with 80% and 100% warnings, category and six-month dashboards, CSV preview/mapping/import with row-level errors and deduplication, monthly CSV export, seed data, tests, and responsive UI.

With more time I would add recurring transaction rules, password reset, persistent sessions, and deployment configuration with HTTPS.

## AI tools

OpenCode (GPT-6 Astra) was used to scaffold the full-stack implementation, write the accounting logic and automated tests, and review the responsive UI. The data model, validation rules, and ledger behavior were manually checked.

## Environment

Copy `.env.example` when deploying or customizing local settings. Never commit secrets.
