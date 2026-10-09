# Signing and notarizing the macOS binary

`scripts/build_executable.sh` only ad-hoc signs. For a binary that opens without the Gatekeeper
warning you need an Apple Developer ID (paid account). Run these by hand after the build:

```bash
codesign --force --options runtime --timestamp --sign "Developer ID Application: NAME (TEAMID)" dist/reclaim
ditto -c -k --keepParent dist/reclaim dist/reclaim-notarize.zip
xcrun notarytool submit dist/reclaim-notarize.zip --keychain-profile "reclaim-notary" --wait
# Command-line binaries cannot be stapled; Gatekeeper checks the notarization online.
```

Create the `reclaim-notary` profile once with `xcrun notarytool store-credentials`.
