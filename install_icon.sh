#!/bin/bash
# Replace LocalFlow's icon with your own image.
#
#   ./install_icon.sh ~/Downloads/localflow-icon.png
#
# Takes any square PNG (1024x1024 is ideal), builds every size an .icns needs,
# installs it into the app bundle and nudges Finder/Dock to forget the old one.
set -euo pipefail

SRC="${1:?usage: ./install_icon.sh <square-image.png>}"
APP="${2:-$HOME/Applications/LocalFlow.app}"
[ -f "$SRC" ] || { echo "no such file: $SRC" >&2; exit 1; }
[ -d "$APP" ] || { echo "no app bundle at $APP — run ./make_app.sh first" >&2; exit 1; }

SET="$(mktemp -d)/LocalFlow.iconset"
mkdir -p "$SET"
for size in 16 32 128 256 512; do
  sips -z $size $size      "$SRC" --out "$SET/icon_${size}x${size}.png"     >/dev/null
  sips -z $((size*2)) $((size*2)) "$SRC" --out "$SET/icon_${size}x${size}@2x.png" >/dev/null
done

iconutil -c icns "$SET" -o "$APP/Contents/Resources/LocalFlow.icns"
rm -rf "$(dirname "$SET")"

# Icons are cached hard; re-register and restart the UI bits that show them.
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP" 2>/dev/null || true
killall Finder Dock 2>/dev/null || true

# Keep the source image so make_app.sh reuses it instead of the drawn fallback.
cp "$SRC" "$(dirname "$0")/icon.png"
echo "installed icon from $SRC — saved as icon.png for future rebuilds"
