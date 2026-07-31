"""Unit tests for the gate preview-card format helpers (agent/_describe_format.py).

Extracted from gate.py so they're importable headless: gate.py pulls Rhino-only
deps, but _describe_format only needs the stdlib. Loaded directly via importlib
(same dance as test_message_flattener.py) — no Rhino, no new dependency.

Run (from rhaino/plugin):
    python backend/tests/test_describe_format.py
or:
    python -m unittest -v backend.tests.test_describe_format   # if backend importable
"""
from __future__ import annotations

import importlib.util
import os
import unittest

_MOD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "agent", "_describe_format.py"
)
_spec = importlib.util.spec_from_file_location("describe_format_under_test", _MOD_PATH)
df = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(df)


class FmtNumTests(unittest.TestCase):
    def test_strips_trailing_zeros(self):
        self.assertEqual(df._fmt_num(12.0), "12")
        self.assertEqual(df._fmt_num(5.50), "5.5")
        self.assertEqual(df._fmt_num(-3.25), "-3.25")

    def test_zero(self):
        self.assertEqual(df._fmt_num(0), "0")
        self.assertEqual(df._fmt_num(0.0), "0")

    def test_string_number(self):
        self.assertEqual(df._fmt_num("7"), "7")

    def test_non_number_is_none(self):
        self.assertIsNone(df._fmt_num(None))
        self.assertIsNone(df._fmt_num("abc"))
        self.assertIsNone(df._fmt_num([1, 2]))

    def test_nan_and_inf_are_none(self):
        # The session fix: not just NaN, also +/-inf must be dropped.
        self.assertIsNone(df._fmt_num(float("nan")))
        self.assertIsNone(df._fmt_num(float("inf")))
        self.assertIsNone(df._fmt_num(float("-inf")))


class CountTests(unittest.TestCase):
    def test_singular(self):
        self.assertEqual(df._count(1, "Kante", "Kanten"), "1 Kante")

    def test_plural(self):
        self.assertEqual(df._count(3, "Kante", "Kanten"), "3 Kanten")

    def test_zero_is_plural(self):
        self.assertEqual(df._count(0, "Kante", "Kanten"), "0 Kanten")


class IntListTests(unittest.TestCase):
    def test_single_value(self):
        self.assertEqual(df._int_list(5), [5])

    def test_list(self):
        self.assertEqual(df._int_list([1, 2, 3]), [1, 2, 3])

    def test_floats_truncated(self):
        self.assertEqual(df._int_list([1.9, 2.1]), [1, 2])

    def test_bools_excluded(self):
        # bool is an int subclass — must not leak into an index list.
        self.assertEqual(df._int_list([True, 2, False]), [2])
        self.assertEqual(df._int_list(True), [])

    def test_garbage_is_empty(self):
        self.assertEqual(df._int_list(None), [])
        self.assertEqual(df._int_list("x"), [])
        self.assertEqual(df._int_list([{"a": 1}, "y"]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
