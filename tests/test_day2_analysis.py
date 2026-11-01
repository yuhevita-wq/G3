import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("day2", Path(__file__).resolve().parents[1] / "scripts" / "analyze_day2_r1_6.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class AnalysisTests(unittest.TestCase):
    def test_meeting_day_two(self):
        self.assertTrue(m.verify_day("2820230104020001", "2023-01-05", 1))
        self.assertFalse(m.verify_day("2820230104020001", "2023-01-04", 1))
        self.assertFalse(m.verify_day("2820230104010001", "2023-01-05", 1))

    def test_same_line_and_order(self):
        formation = "1-7-9/2-5-6/8-3-4"
        self.assertEqual(m.classify("1-7-9", formation)["full_line_class"], "AAA（3着まで同ライン）")
        self.assertEqual(m.classify("7-1-8", formation)["order_class"], "逆筋")
        self.assertEqual(m.classify("1-7-8", formation)["order_class"], "順筋")
        self.assertEqual(m.classify("1-7-8", formation)["full_line_class"], "AAB（1・2着同ライン）")
        self.assertEqual(m.classify("1-2-7", formation)["full_line_class"], "ABA（1・3着同ライン）")
        self.assertEqual(m.classify("2-1-7", formation)["full_line_class"], "ABB（2・3着同ライン）")
        self.assertEqual(m.classify("1-2-8", formation)["full_line_class"], "ABC（3人別ライン）")

    def test_missing_and_duplicate_lines(self):
        self.assertEqual(m.classify("1-2-3", "1-2/1-3")["line_class"], "判定不能")
        self.assertEqual(m.classify("1-2-3", "")["line_class"], "判定不能")

    def test_descriptive_statistics(self):
        s = m.summary([100, 200, 200, 400, 900])
        self.assertEqual(s["median"], 200)
        self.assertEqual(s["mode_values"], [200])
        self.assertEqual(s["p25"], 200)
        self.assertEqual(s["p75"], 400)
        self.assertEqual(m.bar_distribution([{"payout_yen": 200}, {"payout_yen": 20000}])[0]["count"], 1)


if __name__ == "__main__":
    unittest.main()
