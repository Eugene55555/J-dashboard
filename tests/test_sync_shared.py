"""Сверка общей части репозиториев: расхождение видно, --apply переносит файлы."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "sync_shared.py"


class SyncShared(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "J"
        self.target = self.tmp / "D"
        for root in (self.source, self.target):
            (root / "app").mkdir(parents=True)
            (root / "app" / "main.py").write_text("print('same')\n", encoding="utf-8")

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT),
                               "--source", str(self.source), "--target", str(self.target), *args],
                              capture_output=True, text=True, check=False)

    def test_drift_is_reported_and_can_be_applied(self):
        (self.target / "app" / "main.py").write_text("print('old')\n", encoding="utf-8")

        check = self.run_tool("--check")
        self.assertEqual(check.returncode, 1)
        self.assertIn("app/main.py", check.stdout)
        self.assertIn("--apply", check.stdout)

        applied = self.run_tool("--apply")
        self.assertEqual(applied.returncode, 0)
        self.assertEqual((self.target / "app" / "main.py").read_text(encoding="utf-8"),
                         "print('same')\n")

        again = self.run_tool("--check")
        self.assertEqual(again.returncode, 0)
        self.assertIn("совпадает", again.stdout)

    def test_missing_file_is_copied_from_source(self):
        (self.source / "app" / "queries.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.run_tool("--check").returncode, 1)
        self.assertEqual(self.run_tool("--apply").returncode, 0)
        self.assertEqual((self.target / "app" / "queries.py").read_text(encoding="utf-8"),
                         "x = 1\n")

    def test_missing_directory_is_an_error(self):
        result = subprocess.run([sys.executable, str(SCRIPT),
                                 "--source", str(self.tmp / "nope"), "--target", str(self.target)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()