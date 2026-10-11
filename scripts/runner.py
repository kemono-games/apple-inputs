#!/usr/bin/env python3
"""Resolve an exact Xcode release using the reviewed runner-images snapshot."""
import argparse
import json
import os
from pathlib import Path
import re

MAPPING = Path(__file__).resolve().parents[1] / 'xcode-runners.json'


def resolve(version):
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version):
        raise SystemExit('xcode_version requires an exact minor or patch version, e.g. 26.6 or 26.4.1')
    data = json.loads(MAPPING.read_text())
    if version not in data['xcodes']:
        raise SystemExit(f'Xcode {version} is not in the reviewed runner map. '
                         f"Supported versions: {', '.join(data['xcodes'])}. No version substitution is allowed.")
    entry = data['xcodes'][version]
    if entry['runner'] not in data['images']:
        raise SystemExit('Runner mapping has no official image source')
    return {**entry, 'source': data['images'][entry['runner']],
            'runner_images_commit': data['runner_images_commit']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xcode-version', required=True)
    args = parser.parse_args()
    selected = resolve(args.xcode_version)
    print(json.dumps(selected, indent=2))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f"runner={selected['runner']}\n")
