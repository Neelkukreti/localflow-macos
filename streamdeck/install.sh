#!/bin/sh
# Install the LocalFlow push-to-talk plugin into the Stream Deck app.
# Then drag "LocalFlow > Hold to talk" from the action list onto a key.
set -e
SD="$HOME/Library/Application Support/com.elgato.StreamDeck/Plugins"
HERE="$(cd "$(dirname "$0")" && pwd -P)"
osascript -e 'quit app "Elgato Stream Deck"' 2>/dev/null || true
sleep 2
rm -rf "$SD/com.localflow.ptt.sdPlugin"
cp -R "$HERE/com.localflow.ptt.sdPlugin" "$SD/"
open -a "Elgato Stream Deck"
echo "installed — add LocalFlow > Hold to talk to a key"
