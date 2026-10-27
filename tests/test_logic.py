import unittest

from logic import LedgerEntry, ValidationError, account_balances, budget_progress, parse_paise, validate_date, validate_month, validate_transaction_kind


class MoneyTests(unittest.TestCase):
    def test_parse_paise_avoids_float_rounding(self):
        self.assertEqual(parse_paise("1,200.10"), 120010)
        self.assertEqual(parse_paise("0.01"), 1)

    def test_parse_rejects_missing_and_negative_amounts(self):
        with self.assertRaises(ValidationError):
            parse_paise("")
        with self.assertRaises(ValidationError):
            parse_paise("-2")

    def test_zero_is_allowed_only_for_opening_balance(self):
        with self.assertRaises(ValidationError):
            parse_paise("0")
        self.assertEqual(parse_paise("0", allow_zero=True), 0)


class LedgerTests(unittest.TestCase):
    def test_income_and_expense_change_the_correct_account(self):
        result = account_balances([1], [LedgerEntry("income", 10000, 1), LedgerEntry("expense", 2500, 1)])
        self.assertEqual(result, {1: 7500})

    def test_transfer_changes_both_accounts_but_not_total_money(self):
        result = account_balances([1, 2], [LedgerEntry("transfer", 5000, 1, 2)])
        self.assertEqual(result, {1: -5000, 2: 5000})
        self.assertEqual(sum(result.values()), 0)

    def test_unknown_transfer_destination_does_not_create_an_account(self):
        result = account_balances([1], [LedgerEntry("transfer", 5000, 1, 99)])
        self.assertEqual(result, {1: -5000})


class ValidationTests(unittest.TestCase):
    def test_bad_import_date_is_reported(self):
        with self.assertRaises(ValidationError):
            validate_date("31/02/2026")

    def test_month_and_kind_validation(self):
        self.assertEqual(validate_month("2026-10"), "2026-10")
        self.assertEqual(validate_transaction_kind("TRANSFER"), "transfer")
        with self.assertRaises(ValidationError):
            validate_month("October")


class BudgetTests(unittest.TestCase):
    def test_budget_warning_starts_at_eighty_percent(self):
        self.assertEqual(budget_progress(10000, 7999)["level"], "safe")
        self.assertEqual(budget_progress(10000, 8000)["level"], "warning")

    def test_budget_overage_is_explicit(self):
        result = budget_progress(10000, 11000)
        self.assertEqual(result["level"], "over")
        self.assertTrue(result["warn"])
        self.assertEqual(result["percent"], 110)


if __name__ == "__main__":
    unittest.main()
