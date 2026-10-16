import importlib.util
import os
import json
import sys
from types import SimpleNamespace
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import runner

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

    def apps(self, root, versions):
        for name in versions:
            (root / name).mkdir()
        def output(*cmd, env=None):
            app = Path(env['DEVELOPER_DIR']).parents[1].name
            return f'Xcode {versions[app]}\nBuild version 17F113'
        return output

    def test_exact_version_and_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp)
            output = self.apps(apps, {'Xcode_26.6.app': '26.6', 'Xcode_27.app': '27.0'})
            (apps / 'Xcode.app').symlink_to(apps / 'Xcode_26.6.app')
            with patch.object(exporter, 'output', output):
                env, version = exporter.select_xcode('26.6', apps)
                self.assertIn('26.6', env['DEVELOPER_DIR'])
                self.assertEqual(version, 'Xcode 26.6\nBuild version 17F113')
                for missing in ('26.5', '26.6.1', ''):
                    with self.assertRaises(SystemExit):
                        exporter.select_xcode(missing, apps)

    def test_distinct_matching_apps_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp)
            output = self.apps(apps, {'Xcode_A.app': '26.6', 'Xcode_B.app': '26.6'})
            with patch.object(exporter, 'output', output), self.assertRaisesRegex(SystemExit, 'found 2'):
                exporter.select_xcode('26.6', apps)

    def sdk(self, root, sdk, version):
        path = root / sdk
        (path / 'usr/include/c++/v1').mkdir(parents=True)
        (path / 'usr/lib').mkdir()
        (path / 'System/Library/Frameworks/Foundation.framework').mkdir(parents=True)
        (path / 'usr/lib/libSystem.tbd').write_text('system stub')
        (path / 'SDKSettings.json').write_text(json.dumps({'Version': version, 'CanonicalName': sdk + version}))
        for header in ('vector', 'string', '__config'):
            (path / 'usr/include/c++/v1' / header).write_text(header)
        return path

    def sdk_output(self, paths, versions):
        def output(*cmd, env=None):
            return versions[cmd[2]] if cmd[-1] == '--show-sdk-version' else str(paths[cmd[2]])
        return output

    def test_detected_sdks_must_agree(self):
        versions = {'iphoneos': '26.5', 'iphonesimulator': '26.4'}
        with patch.object(exporter, 'output', self.sdk_output({}, versions)):
            with self.assertRaisesRegex(SystemExit, 'versions differ'):
                exporter.detect_sdks({})

    def test_sdk_settings_and_required_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {sdk: self.sdk(Path(tmp), sdk, '26.5') for sdk in ('iphoneos', 'iphonesimulator')}
            versions = dict.fromkeys(paths, '26.5')
            with patch.object(exporter, 'output', self.sdk_output(paths, versions)):
                self.assertEqual(exporter.detect_sdks({'DEVELOPER_DIR': '/xcode'})[0], versions)
                (paths['iphoneos'] / 'usr/lib/libSystem.tbd').unlink()
                with self.assertRaisesRegex(SystemExit, 'Incomplete iphoneos'):
                    exporter.detect_sdks({})
                (paths['iphoneos'] / 'usr/lib/libSystem.tbd').write_text('stub')
                (paths['iphonesimulator'] / 'SDKSettings.json').write_text('{"Version":"26.6"}')
                with self.assertRaisesRegex(SystemExit, 'settings differ'):
                    exporter.detect_sdks({})

    def test_export_uses_detected_sdk_in_tag_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = {sdk: self.sdk(base, sdk, '26.5') for sdk in ('iphoneos', 'iphonesimulator')}
            resource = base / 'toolchain/usr/lib/clang/21'
            (resource / 'lib/darwin').mkdir(parents=True)
            for name in ('libclang_rt.ios.a', 'libclang_rt.iossim.a'):
                (resource / 'lib/darwin' / name).write_bytes(b'runtime')
            sdk_output = self.sdk_output(paths, dict.fromkeys(paths, '26.5'))
            def output(*cmd, env=None):
                if cmd[:2] == ('xcrun', 'clang'):
                    return str(resource) if cmd[-1] == '-print-resource-dir' else 'Apple clang test'
                return sdk_output(*cmd, env=env)
            args = SimpleNamespace(xcode_version='26.6', revision='2', out=base / 'dist')
            with patch.object(exporter.platform, 'system', return_value='Darwin'), \
                    patch.object(exporter, 'select_xcode', return_value=({'DEVELOPER_DIR': '/Xcode'}, 'Xcode 26.6\nBuild version 17F113')), \
                    patch.object(exporter, 'output', output), patch.object(exporter.subprocess, 'run') as run, \
                    patch.dict(os.environ, {}, clear=True):
                exporter.export(args)
            metadata = json.loads((args.out / 'apple-inputs.json').read_text())
            provenance = json.loads((args.out / 'provenance.json').read_text())
            self.assertEqual(provenance['tag'], 'ios-26.5-xcode-26.6-17F113-r2')
            self.assertEqual(metadata['components']['iphoneos']['version'], '26.5')
            self.assertEqual(metadata['components']['iphonesimulator']['version'], '26.5')
            self.assertEqual(set(metadata['components']), {'iphoneos', 'iphonesimulator', 'libcxx', 'compiler_rt'})
            self.assertEqual(run.call_count, 2)
            self.assertEqual(exporter.sha256(args.out / 'apple-inputs.tar.gz'), provenance['archive_sha256'])

    def test_reviewed_runner_mapping(self):
        self.assertEqual(runner.resolve('26.6')['runner'], 'macos-26')
        self.assertEqual(runner.resolve('26.6')['build'], '17F113')
        self.assertEqual(runner.resolve('16.4')['runner'], 'macos-15')
        self.assertEqual(runner.resolve('26.4.1')['runner'], 'macos-26')
        self.assertEqual(runner.resolve('27.0')['build'], '27A266a')
        self.assertEqual(runner.resolve('27.1')['runner'], 'xcode-27')
        self.assertEqual(runner.resolve('27.1')['build'], '27A9275')
        # 27.2 is a beta on the image and is deliberately not mapped.
        for version in ('26', '27', '', 'latest', '26.4', '26.6.1', '27.1.0', '27.2', '26.6;echo'):
            with self.subTest(version=version), self.assertRaises(SystemExit):
                runner.resolve(version)


if __name__ == '__main__':
    unittest.main()
