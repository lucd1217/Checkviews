"""Run with: python3 -m unittest discover tests"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tt")]

import common  # noqa: E402
import total_views  # noqa: E402
import ttviews  # noqa: E402


def entry(vid, views, caption="c", ts=1):
    return {"id": vid, "timestamp": ts, "caption": caption, "views": views}


class ParseTests(unittest.TestCase):
    def test_parse_views(self):
        self.assertEqual(common.parse_views("1,234"), 1234)
        self.assertEqual(common.parse_views(5), 5)
        self.assertIsNone(common.parse_views(None))
        self.assertIsNone(common.parse_views("abc"))

    def test_tiktok_id_strips_query(self):
        self.assertEqual(ttviews.extract_video_id("https://x/@a/video/123?lang=en"), "123")


class HistoryTests(unittest.TestCase):
    def test_new_then_delta(self):
        h = common.update_history({}, [entry("a", 100)])
        common.update_history(h, [entry("a", 150)])
        self.assertEqual(h["a"]["views_delta"], 50)

    def test_rerun_keeps_delta(self):
        h = common.update_history({}, [entry("a", 100)])
        common.update_history(h, [entry("a", 150)])
        common.update_history(h, [entry("a", 150)])
        self.assertEqual(h["a"]["views_delta"], 50)

    def test_negative_delta_recorded(self):
        h = common.update_history({}, [entry("a", 100)])
        common.update_history(h, [entry("a", 90)])
        self.assertEqual(h["a"]["views_delta"], -10)

    def test_missing_only_on_complete_fetch(self):
        h = common.update_history({}, [entry("a", 1), entry("b", 2)])
        common.update_history(h, [entry("a", 1)], complete=False)
        self.assertEqual(h["b"]["status"], "ACTIVE")
        common.update_history(h, [entry("a", 1)], complete=True)
        self.assertEqual(h["b"]["status"], "MISSING")
        self.assertEqual(h["b"]["views"], 2)

    def test_duplicates_in_one_fetch(self):
        h = common.update_history({}, [entry("a", 100)])
        common.update_history(h, [entry("a", 150), entry("a", 150)])
        self.assertEqual(h["a"]["views_delta"], 50)

    def test_fills_views_when_previously_unknown(self):
        h = common.update_history({}, [entry("a", None)])
        common.update_history(h, [entry("a", 40)])
        self.assertEqual(h["a"]["views"], 40)

    def test_legacy_string_views_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.json"
            common.save_json(p, {"a": {"views": "1,200", "status": "ACTIVE"}})
            self.assertEqual(common.load_history(p)["a"]["views"], 1200)


class FetchTests(unittest.TestCase):
    def test_empty_fetch_raises_and_keeps_old_raw(self):
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "raw.jsonl"
            raw.write_text('{"id": "old"}\n')
            with self.assertRaises(common.FetchError):
                common.fetch_entries(["true"], raw, retries=0, backoff=0, cwd=d)
            self.assertEqual(raw.read_text(), '{"id": "old"}\n')

    def test_missing_binary(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(common.FetchError):
                common.fetch_entries(["no-such-binary-xyz"], Path(d) / "r", 0, 0, d)


class TotalsTests(unittest.TestCase):
    TIERS = [{"up_to": 1000, "rpm": 1.0}, {"up_to": None, "rpm": 2.0}]

    def test_earnings_tiers(self):
        self.assertAlmostEqual(total_views.earnings(500, self.TIERS), 0.5)
        self.assertAlmostEqual(total_views.earnings(1500, self.TIERS), 1.0 + 1.0)

    def test_delta_straddling_threshold(self):
        before = total_views.earnings(900, self.TIERS)
        after = total_views.earnings(1100, self.TIERS)
        self.assertAlmostEqual(after - before, 0.1 + 0.2)

    def test_compute_delta_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "log.json"
            delta, log = total_views.compute_delta(p, 100)
            self.assertEqual(delta, 100)
            common.save_json(p, log)
            delta, log = total_views.compute_delta(p, 130)
            self.assertEqual(delta, 30)
            common.save_json(p, log)
            delta, _ = total_views.compute_delta(p, 130)
            self.assertEqual(delta, 30)  # not 0


if __name__ == "__main__":
    unittest.main()
