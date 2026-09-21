#!/bin/bash
# Build ~/Applications/LocalFlow.app so Spotlight can launch LocalFlow.
#
# Uses py2app in ALIAS mode: the bundle references the sources in this folder
# rather than copying the 1.1 GB venv, so editing the Python here takes effect
# on the next launch. Re-run this only if you change the icon or setup.py.
#
# Why py2app and not a shell script that execs python: the bundle needs its own
# executable. A launcher script leaves the running process as Python.app, so
# macOS shows Python's name and icon and files the Microphone / Accessibility
# grants under "Python" instead of "LocalFlow".
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")" && pwd -P)"
APP="${1:-$HOME/Applications/LocalFlow.app}"
cd "$PROJECT"

# Icon: your own icon.png if present, otherwise the one make_icon.py draws.
SET="$(mktemp -d)/LocalFlow.iconset"
mkdir -p "$SET"
if [ -f icon.png ]; then
  for size in 16 32 128 256 512; do
    sips -z $size $size icon.png --out "$SET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size*2)) $((size*2)) icon.png --out "$SET/icon_${size}x${size}@2x.png" >/dev/null
  done
else
  ./.venv/bin/python make_icon.py "$SET" >/dev/null
fi
iconutil -c icns "$SET" -o LocalFlow.icns
rm -rf "$(dirname "$SET")"

rm -rf build dist
./.venv/bin/python setup.py py2app -A >/dev/null

# Don't clobber a running copy in place — stop it first.
pkill -f "LocalFlow.app/Contents/MacOS/LocalFlow" 2>/dev/null || true
sleep 1
rm -rf "$APP"
cp -R dist/LocalFlow.app "$APP"
# Alias mode symlinks the icon back into this folder; a real file is one less
# thing to break, and Finder reads it more reliably.
rm -f "$APP/Contents/Resources/LocalFlow.icns"
cp LocalFlow.icns "$APP/Contents/Resources/LocalFlow.icns"

# Tell Launch Services about it and drop the cached icon.
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP" 2>/dev/null || true
killall Dock 2>/dev/null || true

# Leaving dist/ behind gives Spotlight a second "LocalFlow" to offer.
rm -rf build dist

echo "built $APP"
echo "Launch it from Spotlight. First run asks for Microphone, Accessibility and"
echo "Input Monitoring — grant them to LocalFlow (grants given to Python do not carry over)."
