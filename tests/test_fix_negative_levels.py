"""Разовый ремонт уровней минус-таргетов: видит расхождение, правит, не трогает чужое."""
import sqlite3
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "fix_negative_levels.py"

SCHEMA = """
CREATE TABLE dim_target (
    target_id TEXT PRIMARY KEY,
    campaign_id TEXT,
    ad_group_id TEXT,
    kind TEXT,
    targeting TEXT,
    match_type TEXT,
    is_negative INTEGER,
    negative_level TEXT,
    state TEXT
);
"""


class FixNegativeLevels(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = self.tmp / "account.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO dim_target VALUES (?,?,?,?,?,?,?,?,?)",
            [
                # кампанийный минус, загруженный до исправления — чинить
                ("t1", "c1", "", "PRODUCT", "ASIN_SAME_AS:B1", "", 1, "AD_GROUP", "ENABLED"),
                ("t2", "c1", None, "KEYWORD", "bad", "", 1, "AD_GROUP", "ENABLED"),
                # правильный кампанийный минус — не трогать
                ("t3", "c1", "", "PRODUCT", "ASIN_SAME_AS:B2", "", 1, "CAMPAIGN", "ENABLED"),
                # групповой минус — не трогать
                ("t4", "c1", "g1", "PRODUCT", "ASIN_SAME_AS:B3", "", 1, "AD_GROUP", "ENABLED"),
                # обычная цель — не трогать
                ("t5", "c1", "g1", "PRODUCT", "ASIN_SAME_AS:B4", "", 0, None, "ENABLED"),
            ],
        )
        conn.commit()
        conn.close()

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), "--db", str(self.db), *args],
                              capture_output=True, text=True, check=False)

    def levels(self):
        conn = sqlite3.connect(self.db)
        try:
            return dict(conn.execute("SELECT target_id, negative_level FROM dim_target"))
        finally:
            conn.close()

    def test_check_reports_without_changing_anything(self):
        before = self.levels()
        result = self.run_tool("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("2", result.stdout)
        self.assertEqual(self.levels(), before)

    def test_apply_fixes_only_campaign_level_negatives(self):
        result = self.run_tool("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.levels(), {
            "t1": "CAMPAIGN", "t2": "CAMPAIGN", "t3": "CAMPAIGN",
            "t4": "AD_GROUP", "t5": None,
        })

    def test_apply_is_idempotent_and_check_goes_green(self):
        self.assertEqual(self.run_tool("--apply").returncode, 0)
        second = self.run_tool("--apply")
        self.assertEqual(second.returncode, 0)
        self.assertIn("нечего", second.stdout)
        self.assertEqual(self.run_tool("--check").returncode, 0)

    def test_missing_db_is_an_error(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--db", str(self.tmp / "нет.db")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)

    def test_conflicting_arguments_are_refused(self):
        result = self.run_tool("--check", "--apply")
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()