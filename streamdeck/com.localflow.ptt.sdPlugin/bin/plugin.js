// LocalFlow push-to-talk for Stream Deck.
//
// The Stream Deck app tells a plugin when a key goes down AND when it comes
// back up; the built-in Open/Hotkey actions only fire once per press, which
// is why hold-to-talk needs a plugin at all. We turn those two events into
// Darwin notifications that LocalFlow listens for (see remote.py):
//
//   key down -> com.localflow.start     key up -> com.localflow.finish
//
// notifyutil ships with macOS and needs no Accessibility/Automation grant.
"use strict";

const { execFile } = require("child_process");
const WebSocket = require("ws");

const ACTION = "com.localflow.ptt.hold";
const NOTIFYUTIL = "/usr/bin/notifyutil";

// Stream Deck launches us with: -port N -pluginUUID U -registerEvent E -info J
const args = {};
for (let i = 2; i < process.argv.length - 1; i += 2) {
  args[process.argv[i].replace(/^-+/, "")] = process.argv[i + 1];
}

function post(name) {
  execFile(NOTIFYUTIL, ["-p", "com.localflow." + name], () => {});
}

// Keys currently held, so a release (or a page switch mid-hold) always ends
// exactly the dictation it started — never a stray "finish".
const held = new Set();

const ws = new WebSocket("ws://127.0.0.1:" + args.port);

ws.on("open", () => {
  ws.send(JSON.stringify({ event: args.registerEvent, uuid: args.pluginUUID }));
});

ws.on("message", (raw) => {
  let msg;
  try { msg = JSON.parse(raw.toString()); } catch { return; }
  if (msg.action !== ACTION) return;

  if (msg.event === "keyDown") {
    held.add(msg.context);
    post("start");
  } else if (msg.event === "keyUp" || msg.event === "willDisappear") {
    // willDisappear: the page changed or the profile switched while held.
    if (held.delete(msg.context)) post("finish");
  }
});

// If the Stream Deck app goes away mid-hold, don't leave LocalFlow recording.
ws.on("close", () => {
  if (held.size) post("finish");
  process.exit(0);
});
ws.on("error", () => process.exit(1));
