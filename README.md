# Apple iOS inputs

Manually export matching iPhoneOS and iPhoneSimulator SDKs, libc++ headers and
Darwin compiler-rt from the Xcode installations on GitHub's `macos-26` image.
Inputs are published as GitHub Release assets, never Actions artifacts.

Run **Release Apple iOS inputs** with the exact SDK version and optional Xcode
version. Defaults are SDK **26.5** and Xcode **26.6**. These version numbers are
not interchangeable. The workflow inventories installed Xcodes and fails if
there is no exact match, or if an SDK-only selection is ambiguous. It does not
download arbitrary Xcode versions. Availability depends on the runner image.

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
