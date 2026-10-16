# Apple iOS inputs

Export iPhoneOS and iPhoneSimulator SDKs, libc++ headers and Darwin compiler-rt
from an exact Xcode release on a compatible GitHub-hosted macOS runner. Inputs
are published directly as GitHub Release assets, never Actions artifacts.

Run **Release Apple iOS inputs** with required `xcode_version` (default **27.1**)
and `revision`. There is no SDK version input. The exporter reads the actual
Xcode version/build with `xcodebuild -version`, then independently reads both SDK
versions with `xcrun`. The SDK versions must agree and match their SDKSettings.
For Xcode **26.6 (17F113)**, the currently verified SDK version is **26.5**;
SDK version numbers are never inferred from Xcode version numbers.

`xcode-runners.json` is the reviewed compatibility map. A read-only Ubuntu job
resolves the exact version; the release job uses its macOS runner output as
`runs-on`. It never constructs a label from the Xcode version or uses `latest`.
The map records the official runner-images commit and per-image source URLs:

| Exact Xcode versions | Runner | Official inventory |
| --- | --- | --- |
| 16.0, 16.1, 16.2, 16.3, 16.4 | macos-15 | [macOS 15 arm64](https://github.com/actions/runner-images/blob/e3fa5414d230773024d4a669a8402b50cd28f036/images/macos/macos-15-arm64-Readme.md) |
| 26.0.1, 26.1.1, 26.2, 26.3, 26.4.1, 26.5, 26.6 | macos-26 | [macOS 26 arm64](https://github.com/actions/runner-images/blob/e3fa5414d230773024d4a669a8402b50cd28f036/images/macos/macos-26-arm64-Readme.md) |
| 27.0 (27A266a), 27.1 (27A9275) | xcode-27 | [Xcode 27 arm64](https://github.com/actions/runner-images/blob/e3fa5414d230773024d4a669a8402b50cd28f036/images/macos/xcode-27-arm64-Readme.md) |

Only these exact version strings are accepted. For example, `26.4` is not an
alias for `26.4.1`, and `26.6.0` is not substituted for `26.6`. Unknown versions,
missing installations, multiple distinct matching installations, unexpected
Xcode builds, incomplete SDKs or mismatched SDK versions fail explicitly.
Filesystem aliases resolving to the same installation count only once.
There is no automatic minor/major upgrade or fallback between Xcode lines.

`xcode-27` is GitHub's Xcode 27 image (macOS 27; still labelled a public
preview in [actions/runner-images#14404](https://github.com/actions/runner-images/issues/14404)).
Its 27.1 is build 27A9275, installed as `Xcode_27.1_Release_Candidate.app`
(observed by the 2026-10-16 export run; the inventory at the pinned commit
still lists the earlier 27A9269). Any other 27.1 build is a different input and
is refused, not substituted. Its 27.2 is a beta and is not mapped.

Hosted images change over time. To add a version, review the official image
inventory, update the map's commit/source and exact version/build, and validate
that version on CI. A documented mapping is not proof that every listed version
has completed this export workflow; the default release verification
exercises only 27.1.

```sh
gh workflow run release.yml --repo kemono-games/apple-inputs --ref main \
  -f xcode_version=27.1 -f revision=1
# Example patch-version selection (not release-validated here):
# gh workflow run release.yml --repo kemono-games/apple-inputs --ref main \
#   -f xcode_version=26.4.1 -f revision=1
python3 scripts/export.py --xcode-version 26.6 --revision 2 --out dist
```

Use an unused revision when running the example again. For local tests:
`python3 -m unittest discover -s tests`.

Release tags include SDK version, Xcode version/build and a revision, for example
`ios-26.5-xcode-26.6-17F113-r1`. Existing releases/tags are not overwritten.
A failed upload leaves a draft for diagnosis; use a new revision to retry.

Assets:

- `apple-inputs.tar.gz`: `iphoneos/`, `iphonesimulator/`, `darwin/`,
  `apple-inputs.json` and complete `SHA256SUMS`.
- `apple-inputs.sha256`: transport checksum.
- `apple-inputs.json`: SDK, libc++, compiler-rt versions, sources and content hashes.
- `provenance.json`: producer commit/run and archive checksum.

Consumers should pin a full release URL and its archive SHA256 in source control,
then also verify the extracted file inventory and expected component hashes.
Do not resolve `latest` during a reproducible build. Publishing these inputs is
not cross-toolchain qualification or evidence that an iOS application runs.

```sh
curl -fL -o apple-inputs.tar.gz https://github.com/kemono-games/apple-inputs/releases/download/ios-26.5-xcode-26.6-17F113-r1/apple-inputs.tar.gz
curl -fL -o apple-inputs.sha256 https://github.com/kemono-games/apple-inputs/releases/download/ios-26.5-xcode-26.6-17F113-r1/apple-inputs.sha256
shasum -a 256 -c apple-inputs.sha256
```

SDK and runtime files retain their upstream ownership and licensing. This
repository does not relicense bundled Apple or LLVM components.
