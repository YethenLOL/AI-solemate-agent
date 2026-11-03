# macOS packaging

## Build

```bash
bash scripts/setup.sh
bash scripts/build_app.sh
open dist/Dafeiyu.app
```

`scripts/build_app.sh`:

1. installs `requirements-app.txt` (PyInstaller + Pillow);
2. generates `assets/Dafeiyu.iconset`/`Dafeiyu.icns`;
3. runs PyInstaller in windowed mode with `personas/` and `.env.example` bundled;
4. sets bundle name/version;
5. ad-hoc signs the app.

Output: `dist/Dafeiyu.app` (Apple Silicon when built on arm64).

## Where user data lives

The app sets `DAFEIYU_HOME=~/Library/Application Support/Dafeiyu` before importing the backend:

```text
~/Library/Application Support/Dafeiyu/
├── .env                 # API key, QQ numbers, token (never commit)
├── data/dafeiyu.db      # chat, facts, videos, activity, audit
└── personas/dafeiyu.md  # editable persona card
```

The `.app` bundle contains only code, default persona, and `.env.example`; it contains no user credentials or databases.

## Gatekeeper

The default build is **ad-hoc signed and not notarized**. macOS may block the first launch:

- Right-click the app → **Open** → **Open**; or
- System Settings → Privacy & Security → **Open Anyway**.

macOS permissions the app may request:

- **Accessibility** — read the frontmost app/window title and send automation keystrokes.
- **Automation** — control `System Events` and `Music`.
- **Local Network** — not required for `127.0.0.1`; NapCat runs locally.

## Developer ID signing / notarization

For a public release, you need an Apple Developer account:

```bash
codesign --force --deep --options runtime \
  --sign "Developer ID Application: Your Name (TEAMID)" \
  dist/Dafeiyu.app

ditto -c -k --sequesterRsrc --keepParent dist/Dafeiyu.app dist/Dafeiyu-1.1.0-macos-arm64.zip

xcrun notarytool submit dist/Dafeiyu.zip \
  --apple-id "you@example.com" \
  --team-id "TEAMID" \
  --password "app-specific-password" \
  --wait

xcrun stapler staple dist/Dafeiyu.app
```

PyInstaller's nested binaries may need explicit signing/entitlements for hardened runtime. Review `codesign --deep` output and Apple's notarization logs.

## GitHub Release

```bash
git tag v1.1.0
git push origin v1.1.0
```

Attach `dist/Dafeiyu-1.1.0-macos-arm64.zip` to the release. Include:

- macOS version and architecture (arm64 / Intel);
- NapCat version tested;
- known limitations (unsigned, no bundled QQ/NapCat);
- checksum (SHA-256).

Do **not** commit `dist/`, `build/`, `*.spec`, `.env`, or `data/`; they are gitignored.
