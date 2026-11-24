import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_static


class ProtectedShellTest(unittest.TestCase):
    def test_cleartext_blocked_in_whole_pages_tree_before_loading_data(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(build_static, 'ROOT', Path(tmp)), patch.object(build_static.settings.__class__, 'accounts', side_effect=AssertionError('доступ к базе до проверки пути')):
            for relative in ('docs', 'docs/preview', 'docs/a/b'):
                out = Path(tmp, relative)
                with self.subTest(relative=relative), self.assertRaises(SystemExit):
                    build_static.build('test', out)
                self.assertFalse(out.exists())

    def test_refresh_only_shell_without_credentials_or_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ciphertext = out / 'data.enc.json'
            ciphertext.write_bytes(b'opaque')
            build_static.refresh_ui(out)
            html = (out / 'index.html').read_text()
            self.assertIn('data.enc.json', html)
            self.assertIn('encrypted: true', html)
            self.assertRegex(html, r'static/app\.js\?v=[0-9a-f]+')
            self.assertEqual(ciphertext.read_bytes(), b'opaque')
            self.assertFalse((out / 'data.json').exists())

    def test_refuse_mixed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'data.enc.json').touch()
            (out / 'data.json').touch()
            with self.assertRaises(SystemExit):
                build_static.refresh_ui(out)
