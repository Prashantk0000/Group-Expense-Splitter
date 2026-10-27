import unittest

from logic import ValidationError, calculate_balances, parse_paise, settle_up, split_expense, validate_date


class MoneyTests(unittest.TestCase):
    def test_parse_paise_avoids_float_rounding(self):
        self.assertEqual(parse_paise("1,200.10"), 120010)
        self.assertEqual(parse_paise("0.01"), 1)

    def test_parse_rejects_missing_negative_and_extra_precision(self):
        for value in ("", "-2", "1.001"):
            with self.assertRaises(ValidationError):
                parse_paise(value)

    def test_zero_is_only_allowed_when_explicit(self):
        with self.assertRaises(ValidationError):
            parse_paise("0")
        self.assertEqual(parse_paise("0", allow_zero=True), 0)


class SplitTests(unittest.TestCase):
    def test_equal_split_assigns_remainder_without_losing_a_paise(self):
        result = split_expense(10001, [1, 2, 3], "equal")
        self.assertEqual(result, {1: 3334, 2: 3334, 3: 3333})
        self.assertEqual(sum(result.values()), 10001)

    def test_exact_split_must_match_total(self):
        self.assertEqual(split_expense(10000, [1, 2], "exact", ["40", "60"]), {1: 4000, 2: 6000})
        with self.assertRaises(ValidationError):
            split_expense(10000, [1, 2], "exact", ["40", "50"])

    def test_percentage_split_is_exact_even_after_rounding(self):
        result = split_expense(10001, [1, 2, 3], "percentage", ["33.33", "33.33", "33.34"])
        self.assertEqual(sum(result.values()), 10001)
        self.assertEqual(result[3], 3335)
        with self.assertRaises(ValidationError):
            split_expense(10000, [1, 2], "percentage", ["20", "20"])


class BalanceTests(unittest.TestCase):
    def test_balances_include_what_was_paid_and_consumed(self):
        balances = calculate_balances([1, 2], [(1, 10000, {1: 5000, 2: 5000})], [])
        self.assertEqual(balances, {1: 5000, 2: -5000})

    def test_recorded_payment_reduces_debt(self):
        balances = calculate_balances([1, 2], [(1, 10000, {1: 5000, 2: 5000})], [(2, 1, 2500)])
        self.assertEqual(balances, {1: 2500, 2: -2500})

    def test_settle_up_clears_balances(self):
        plan = settle_up({1: 7000, 2: 3000, 3: -4000, 4: -6000})
        self.assertEqual(len(plan), 3)
        for payment in plan:
            self.assertGreater(payment["amount_paise"], 0)

    def test_settle_up_uses_fewer_payments_for_cancellation(self):
        plan = settle_up({1: 1000, 2: 1000, 3: -1000, 4: -1000})
        self.assertEqual(len(plan), 2)


class ValidationTests(unittest.TestCase):
    def test_bad_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_date("31/02/2026")


if __name__ == "__main__":
    unittest.main()
