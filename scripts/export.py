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


def select_xcode(sdk_version, xcode_version, applications=Path('/Applications')):
    candidates = []
    observed = []
    # Resolve aliases so Xcode.app and Xcode_26.6.app count as one installation.
    for app in sorted({p.resolve() for p in applications.glob('Xcode*.app')}):
        env = dict(os.environ, DEVELOPER_DIR=str(app / 'Contents/Developer'))
        try:
            version = output('xcodebuild', '-version', env=env)
            sdks = {sdk: output('xcrun', '--sdk', sdk, '--show-sdk-version', env=env)
                    for sdk in ('iphoneos', 'iphonesimulator')}
        except subprocess.CalledProcessError:
            continue
        observed.append({'path': str(app), 'xcode': version, 'sdks': sdks})
        if all(v == sdk_version for v in sdks.values()) and (
                not xcode_version or version.splitlines()[0] == f'Xcode {xcode_version}'):
            candidates.append((env, version))
    print(json.dumps({'installed_xcodes': observed}, indent=2), flush=True)
    if len(candidates) != 1:
        raise SystemExit(f'Expected exactly one matching Xcode, found {len(candidates)}. '
                         'Specify xcode_version to disambiguate; unavailable SDKs are not substituted.')
    return candidates[0]


def export(args):
    if platform.system() != 'Darwin':
        raise SystemExit('Export requires macOS')
    for value in (args.sdk_version, args.xcode_version):
        if value and not re.fullmatch(r'\d+(?:\.\d+){1,2}', value):
            raise SystemExit('Versions must be numeric, for example 26.5 or 26.6')
    if not re.fullmatch(r'[1-9]\d*', args.revision):
        raise SystemExit('Revision must be a positive integer')
    env, version = select_xcode(args.sdk_version, args.xcode_version)
    xcode = version.splitlines()[0].removeprefix('Xcode ')
    build = version.splitlines()[1].removeprefix('Build version ')
    if not re.fullmatch(r'[A-Za-z0-9]+', build):
        raise SystemExit(f'Unexpected Xcode build: {build}')
    tag = f'ios-{args.sdk_version}-xcode-{xcode}-{build}-r{args.revision}'
    root = args.out / 'apple'
    root.mkdir(parents=True, exist_ok=False)
    source = f'https://developer.apple.com/download/all/?q=Xcode%20{xcode}'
    resource = Path(output('xcrun', 'clang', '-print-resource-dir', env=env))
    toolchain = resource.parents[2]
    components = {}
    for sdk in ('iphoneos', 'iphonesimulator'):
        sdk_source = Path(output('xcrun', '--sdk', sdk, '--show-sdk-path', env=env))
        shutil.copytree(sdk_source, root / sdk, symlinks=False)
        headers = root / sdk / 'usr/include/c++/v1'
        if not (headers / 'vector').is_file():
            shutil.copytree(toolchain / 'include/c++/v1', headers, symlinks=False, dirs_exist_ok=True)
        for header in ('vector', 'string', '__config'):
            if not (headers / header).is_file():
                raise SystemExit(f'Missing libc++ header: {sdk}/{header}')
        components[sdk] = {'version': args.sdk_version, 'url': source,
                           'sha256': tree_hash(root / sdk), 'digest_kind': 'file-inventory',
                           'xcode': version}
    darwin = root / 'darwin'
    darwin.mkdir()
    for name in ('libclang_rt.ios.a', 'libclang_rt.iossim.a'):
        shutil.copy2(resource / 'lib/darwin' / name, darwin / name)
        # Consumers require arm64; other runtime slices are preserved as input bytes.
        subprocess.run(['xcrun', 'lipo', '-verify_arch', 'arm64', str(darwin / name)],
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
    provenance = {'schema': 1, 'tag': tag, 'sdk_version': args.sdk_version,
                  'xcode': version, 'archive_sha256': digest,
                  'source_commit': os.environ.get('GITHUB_SHA'),
                  'run_url': f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"}
    write_json(args.out / 'provenance.json', provenance)
    (args.out / 'release-notes.md').write_text(
        f'iPhoneOS and iPhoneSimulator SDK **{args.sdk_version}**, exported from '
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
    parser.add_argument('--sdk-version', required=True)
    parser.add_argument('--xcode-version', default='')
    parser.add_argument('--revision', default='1')
    parser.add_argument('--out', type=Path, default=Path('dist'))
    export(parser.parse_args())
