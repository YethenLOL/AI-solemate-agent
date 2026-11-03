#!/usr/bin/env bash
# 构建 macOS .app（ad-hoc 签名，未公证）
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" -m pip install -q -r requirements-app.txt

"$PYTHON" scripts/make_icon.py
iconutil -c icns assets/Dafeiyu.iconset -o assets/Dafeiyu.icns

"$PYTHON" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name Dafeiyu \
  --osx-bundle-identifier com.dafeiyu.console \
  --icon assets/Dafeiyu.icns \
  --paths . \
  --add-data "personas:personas" \
  --add-data ".env.example:." \
  --hidden-import dotenv \
  scripts/mac_app_entry.py

PLIST="dist/Dafeiyu.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 1.1.0" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion 2" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 大肥鱼" "$PLIST" 2>/dev/null || true
# 双击 App 后自动启动机器人
/usr/libexec/PlistBuddy -c "Add :LSEnvironment dict" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSEnvironment:DAFEIYU_AUTOSTART string 1" "$PLIST" 2>/dev/null || true

codesign --force --deep --sign - dist/Dafeiyu.app
echo "✅ 已生成 dist/Dafeiyu.app"
