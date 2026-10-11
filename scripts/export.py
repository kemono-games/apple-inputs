#!/usr/bin/env python3
"""Export version-checked iOS SDKs and Darwin runtimes from an installed Xcode."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile

from runner import resolve


def output(*cmd, env=None):
    return subprocess.check_output(cmd, text=True, env=env).strip()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def files(root):
    return sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink())


def tree_hash(root):
    inventory = ''.join(f'{sha256(p)}  {p.relative_to(root)}\n' for p in files(root))
    return hashlib.sha256(inventory.encode()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def write_sums(root):
    (root / 'SHA256SUMS').write_text(''.join(
        f'{sha256(p)}  {p.relative_to(root)}\n' for p in files(root) if p.name != 'SHA256SUMS'))


def pack(root, archive):
    # Normalize transport metadata without altering the SDK bytes.
    with archive.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw,
                                                  mtime=0, compresslevel=1) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for path in sorted(root.rglob('*')):
                info = tarfile.TarInfo(path.relative_to(root).as_posix())
                info.mtime = 0
                if path.is_dir():
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
                else:
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(f'Unexpected non-regular SDK entry: {path}')
                    info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
                    info.size = path.stat().st_size
                    with path.open('rb') as stream:
                        tar.addfile(info, stream)


def select_xcode(xcode_version, applications=Path('/Applications')):
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', xcode_version):
        raise SystemExit('An exact Xcode minor or patch version is required')
    candidates = []
    observed = []
    # Aliases do not create ambiguity, but two distinct matching apps do.
    for app in sorted({p.resolve() for p in applications.glob('Xcode*.app')}):
        env = dict(os.environ, DEVELOPER_DIR=str(app / 'Contents/Developer'))
        try:
            version = output('xcodebuild', '-version', env=env)
        except subprocess.CalledProcessError:
            continue
        observed.append({'path': str(app), 'xcode': version})
        if version.splitlines() and version.splitlines()[0] == f'Xcode {xcode_version}':
            candidates.append((env, version))
    print(json.dumps({'installed_xcodes': observed}, indent=2), flush=True)
    if len(candidates) != 1:
        raise SystemExit(f'Expected exactly one Xcode {xcode_version}, found {len(candidates)}. '
                         'Unavailable or ambiguous versions are not substituted.')
    return candidates[0]


def detect_sdks(env):
    versions = {sdk: output('xcrun', '--sdk', sdk, '--show-sdk-version', env=env)
                for sdk in ('iphoneos', 'iphonesimulator')}
    if any(not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value) for value in versions.values()):
        raise SystemExit(f'Invalid detected SDK versions: {versions}')
    if len(set(versions.values())) != 1:
        raise SystemExit(f'Device and simulator SDK versions differ: {versions}')
    paths = {}
    for sdk, version in versions.items():
        root = Path(output('xcrun', '--sdk', sdk, '--show-sdk-path', env=env))
        settings = root / 'SDKSettings.json'
        if not settings.is_file():
            raise SystemExit(f'Missing SDK settings: {sdk}')
        metadata = json.loads(settings.read_text())
        if metadata.get('Version') != version or metadata.get('CanonicalName') != sdk + version:
            raise SystemExit(f'SDK settings differ from xcrun: {sdk}')
        for required in ('usr/include', 'usr/lib/libSystem.tbd',
                         'System/Library/Frameworks/Foundation.framework'):
            if not (root / required).exists():
                raise SystemExit(f'Incomplete {sdk} SDK: missing {required}')
        paths[sdk] = root
    print(json.dumps({'detected_sdks': versions, 'developer_dir': env['DEVELOPER_DIR']}, indent=2), flush=True)
    return versions, paths


def export(args):
    if platform.system() != 'Darwin':
        raise SystemExit('Export requires macOS')
    selected = resolve(args.xcode_version)
    if os.environ.get('SELECTED_RUNNER', selected['runner']) != selected['runner']:
        raise SystemExit('Scheduled runner differs from the reviewed mapping')
    if not re.fullmatch(r'[1-9]\d*', args.revision):
        raise SystemExit('Revision must be a positive integer')
    env, version = select_xcode(args.xcode_version)
    xcode = version.splitlines()[0].removeprefix('Xcode ')
    if len(version.splitlines()) != 2 or not version.splitlines()[1].startswith('Build version '):
        raise SystemExit(f'Unexpected xcodebuild version output: {version}')
    build = version.splitlines()[1].removeprefix('Build version ')
    if not re.fullmatch(r'[A-Za-z0-9]+', build):
        raise SystemExit(f'Unexpected Xcode build: {build}')
    if build != selected['build']:
        raise SystemExit(f"Xcode build differs from reviewed mapping: {build} != {selected['build']}")
    sdk_versions, sdk_paths = detect_sdks(env)
    sdk_version = sdk_versions['iphoneos']
    tag = f'ios-{sdk_version}-xcode-{xcode}-{build}-r{args.revision}'
    root = args.out / 'apple'
    root.mkdir(parents=True, exist_ok=False)
    source = f'https://developer.apple.com/download/all/?q=Xcode%20{xcode}'
    resource = Path(output('xcrun', 'clang', '-print-resource-dir', env=env))
    toolchain = resource.parents[2]
    components = {}
    for sdk in ('iphoneos', 'iphonesimulator'):
        sdk_source = sdk_paths[sdk]
        shutil.copytree(sdk_source, root / sdk, symlinks=False)
        headers = root / sdk / 'usr/include/c++/v1'
        if not (headers / 'vector').is_file():
            shutil.copytree(toolchain / 'include/c++/v1', headers, symlinks=False, dirs_exist_ok=True)
        for header in ('vector', 'string', '__config'):
            if not (headers / header).is_file():
                raise SystemExit(f'Missing libc++ header: {sdk}/{header}')
        components[sdk] = {'version': sdk_versions[sdk], 'url': source,
                           'sha256': tree_hash(root / sdk), 'digest_kind': 'file-inventory',
                           'xcode': version}
    darwin = root / 'darwin'
    darwin.mkdir()
    for name in ('libclang_rt.ios.a', 'libclang_rt.iossim.a'):
        shutil.copy2(resource / 'lib/darwin' / name, darwin / name)
        # Consumers require arm64; other runtime slices are preserved as input bytes.
        subprocess.run(['xcrun', 'lipo', str(darwin / name), '-verify_arch', 'arm64'],
                       env=env, check=True)
    components['compiler_rt'] = {'version': output('xcrun', 'clang', '--version', env=env).splitlines()[0],
                                'url': source, 'sha256': tree_hash(darwin), 'digest_kind': 'file-inventory'}
    libcxx = {sdk: tree_hash(root / sdk / 'usr/include/c++/v1') for sdk in ('iphoneos', 'iphonesimulator')}
    components['libcxx'] = {'version': version, 'url': source,
                          'sha256': hashlib.sha256(json.dumps(libcxx, sort_keys=True).encode()).hexdigest(),
                          'platform_inventories': libcxx, 'digest_kind': 'platform-inventories'}
    metadata = {'components': components, 'xcode': version}
    write_json(root / 'apple-inputs.json', metadata)
    write_sums(root)
    archive = args.out / 'apple-inputs.tar.gz'
    pack(root, archive)
    digest = sha256(archive)
    (args.out / 'apple-inputs.sha256').write_text(f'{digest}  apple-inputs.tar.gz\n')
    write_json(args.out / 'apple-inputs.json', metadata)
    provenance = {'schema': 1, 'tag': tag, 'sdk_version': sdk_version,
                  'xcode': version, 'archive_sha256': digest,
                  'runner': selected['runner'], 'runner_image_version': os.environ.get('ImageVersion'),
                  'runner_images_source': selected['source'],
                  'requested_xcode_version': args.xcode_version,
                  'source_commit': os.environ.get('GITHUB_SHA'),
                  'run_url': f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"}
    write_json(args.out / 'provenance.json', provenance)
    (args.out / 'release-notes.md').write_text(
        f'iPhoneOS and iPhoneSimulator SDK **{sdk_version}**, exported from '
        f'**{xcode} ({build})**. Includes libc++ headers and Darwin compiler-rt.\n\n'
        f'Archive SHA256: `{digest}`\n\n'
        f'Producer: {provenance["run_url"]}\n\n'
        'This release records input bytes; it does not certify cross-toolchain or application compatibility.\n')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f'tag={tag}\nsha256={digest}\n')
    print(f'{tag}: {digest}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xcode-version', required=True)
    parser.add_argument('--revision', default='1')
    parser.add_argument('--out', type=Path, default=Path('dist'))
    export(parser.parse_args())
