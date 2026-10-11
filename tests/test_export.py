import importlib.util
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('exporter', Path(__file__).parents[1] / 'scripts/export.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class ExportTests(unittest.TestCase):
    def test_archive_reproducible_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'apple'
            (root / 'sdk/.hidden').mkdir(parents=True)
            f = root / 'sdk/.hidden/header'
            f.write_bytes(b'header\x00data')
            exporter.write_sums(root)
            first, second = base / 'one.tar.gz', base / 'two.tar.gz'
            exporter.pack(root, first)
            os.utime(f, (100, 100))
            exporter.pack(root, second)
            self.assertEqual(exporter.sha256(first), exporter.sha256(second))
            with tarfile.open(first) as archive:
                self.assertEqual(archive.extractfile('sdk/.hidden/header').read(), b'header\x00data')
                self.assertIn('sdk/.hidden/header', archive.extractfile('SHA256SUMS').read().decode())

    def test_selection_requires_both_sdk_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp)
            (apps / 'Xcode_26.6.app').mkdir()
            def output(*cmd, env=None):
                if cmd[0] == 'xcodebuild':
                    return 'Xcode 26.6\nBuild version 17F113'
                return '26.5' if cmd[2] == 'iphoneos' else '26.4'
            with patch.object(exporter, 'output', output), self.assertRaises(SystemExit):
                exporter.select_xcode('26.5', '26.6', apps)

    def test_selection_disambiguates_and_resolves_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp)
            (apps / 'Xcode_26.6.app').mkdir()
            (apps / 'Xcode_27.0.app').mkdir()
            (apps / 'Xcode.app').symlink_to(apps / 'Xcode_26.6.app')
            def output(*cmd, env=None):
                if cmd[0] == 'xcodebuild':
                    version = '26.6' if '26.6' in env['DEVELOPER_DIR'] else '27.0'
                    return f'Xcode {version}\nBuild version 17F113'
                return '26.5'
            with patch.object(exporter, 'output', output):
                with self.assertRaises(SystemExit):
                    exporter.select_xcode('26.5', '', apps)
                env, version = exporter.select_xcode('26.5', '26.6', apps)
                self.assertIn('26.6', env['DEVELOPER_DIR'])
                self.assertTrue(version.startswith('Xcode 26.6'))


if __name__ == '__main__':
    unittest.main()
