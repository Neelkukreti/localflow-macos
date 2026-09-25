# LocalFlow — a free, fully local Wispr Flow alternative for macOS

**Offline voice-to-text dictation for Apple Silicon Macs.** Hold a key, talk, and cleaned-up
text appears in whatever app you're in. Speech recognition and AI cleanup both run **on your
Mac** — no cloud, no account, no API keys, no subscription, and nothing you say ever leaves
the machine.

If you're looking for a **Wispr Flow alternative**, a **Superwhisper alternative**, or just
**private speech-to-text on macOS** that doesn't phone home, this is that.

> ⭐ **If LocalFlow saves you some typing, please star the repo** — it's the only way people
> searching for a private dictation app will find it.

```
hold Fn  →  🔴 listening  →  release  →  Whisper (local)
         →  Ollama cleanup (local)    →  pasted at your cursor
```

> **On Windows?** See [LocalFlow for Windows](https://github.com/Neelkukreti/LocalFlow) —
> the same idea built on `faster-whisper`. This repo is the macOS/Apple-Silicon build.

## Why this exists

Cloud dictation apps are excellent and also send a recording of your voice to someone else's
servers, on a subscription. For anyone dictating client notes, medical or legal work, source
code, trading positions or private messages, that's a non-starter. LocalFlow does the same job
with local models.

| | LocalFlow | Typical cloud dictation |
|---|---|---|
| Where audio is processed | Your Mac | Someone's servers |
| Cost | Free | Monthly subscription |
| Works offline (on a plane) | Yes | No |
| Account required | No | Yes |
| Word limits | None | Usually |
| Your voice used for training | Never | Check their policy |

## Features

- **Hold to talk, or double-tap to lock hands-free** — Fn (🌐) or a key you pick.
- **Stream Deck / scripting**: `notifyutil -p com.localflow.toggle` starts or finishes a
  dictation — no keystroke permissions needed. F18 does the same from a keyboard remapper.
- **Local Whisper** (`whisper-large-v3-turbo` via MLX, on the Apple GPU).
- **Local AI cleanup** via Ollama — removes "um", fixes punctuation, obeys "new paragraph".
  If Ollama isn't running it falls back to fast rule-based tidying.
- **Per-app voices** — Slack gets short work-speak, email gets prose, a terminal or an AI chat
  keeps `camelCase`, file paths and flags exactly as spoken.
- **Command Mode** — select text, hold ⌘⌥J, say *"make this shorter"* and it's rewritten in
  place. Or say *"ask perplexity about X"* to open that search.
- **Snippets** — a phrase you say expands into text you'd rather not say.
- **Learned dictionary** — picks up names and jargon you say repeatedly so they're spelled
  your way, with a review pass so it never learns junk.
- **Casing that actually holds** — sentence starts, the pronoun "I", weekdays and months are
  fixed in code rather than left to the model, and your dictionary's own spellings are restored.
  Words with internal capitals (`camelCase`, `iPhone`, `BTC`) are never touched, and the whole
  pass is skipped for the code voice.
- **Hinglish and 90+ languages** — Hinglish comes back in Roman script, not Devanagari, with
  proper Hindi schwa deletion (करना → `karna`, not `karanaa`).
- **A tiny black status pill**, like Wispr Flow's: a sliver when idle (optional), just wide
  enough for a level meter while you talk. Click-through; Settings → *Move* to reposition.
- **Scratchpad** — a floating notepad to dictate into when there's no text field.
- **Never types into password fields** — detects macOS secure input and uses the clipboard.
- **Menu-bar app** with a proper window: status, history, dictionary, snippets, settings.

## Requirements

- **Apple Silicon Mac** (M1 or newer) — MLX is Apple-Silicon only.
- **macOS 12+**
- **Python 3.11 (native arm64)**, e.g. `brew install python@3.11`.
  Anaconda's Intel Python will not work: MLX has no x86 wheel.
- **[Ollama](https://ollama.com)** for AI cleanup (optional — there's a rule-based fallback).

## Install

```bash
git clone https://github.com/Neelkukreti/localflow-macos.git
cd localflow-macos
./setup.sh        # virtualenv, dependencies, Ollama model
./make_app.sh     # builds ~/Applications/LocalFlow.app so Spotlight can find it
```

Then launch **LocalFlow** from Spotlight. The first run downloads the Whisper model (~1.5 GB).

### Permissions

macOS will ask for three, all under System Settings → Privacy & Security:

| Permission | Why |
|---|---|
| **Microphone** | To record you at all |
| **Input Monitoring** | For the Fn key and the global shortcuts |
| **Accessibility** | To paste into other apps, and to read selected text for Command Mode |

Without Input Monitoring the triggers silently do nothing — the Status tab in the app window
tells you exactly which grant is missing and opens the right settings pane.



## Using it

| Gesture | What happens |
|---|---|
| **Hold the trigger key** | Records while held, transcribes on release |
| **Double-tap it** | Hands-free — keeps listening until you tap again |
| **F18**, or `notifyutil -p com.localflow.toggle` | Start / finish a hands-free dictation (Stream Deck) |
| `notifyutil -p com.localflow.stop` | Abandon whatever is in flight |
| **⌘⌥J** | Command Mode |
| **⌘⌥S** | Scratchpad |
| **⌘⌥V / ⌘⌥C** | Paste / copy the last dictation |

**Settings → Trigger** picks the hold key (Fn, either Option, right Command, right Control or
F13–F15) and tunes the hold threshold and double-tap window. Trigger changes need a relaunch —
there's a button for it.

### Stream Deck
Add a **System → Open** action pointing at a one-line script:

```sh
#!/bin/sh
exec /usr/bin/notifyutil -p com.localflow.toggle
```

Press once to start listening, again to transcribe. It's a Darwin notification, so it needs no
Accessibility or Automation grant. (The Stream Deck *Hotkey* action won't work with most apps
that use pynput: pynput's `GlobalHotKeys` silently drops injected keystrokes. LocalFlow uses its
own listener that doesn't, so F18 from a remapper does work.)

## Configuration

Everything lives in `config.json`, created from `config.example.json` on first run, and most of
it is editable from the Settings tab: cue volumes (each of the three beeps separately), language
mode, cleanup level (`off` / `light` / `standard` / `aggressive`), microphone and its fallback
order, whether to read screen context, and whether to pause music while listening.

## How it works

1. `recorder.py` captures mic audio with `sounddevice`, with a ranked fallback if your mic is unplugged.
2. `transcribe.py` runs MLX Whisper on the Apple GPU. Your dictionary and recent phrasing are fed
   in as the `initial_prompt`, which is what keeps names spelled consistently.
3. `cleanup.py` asks a local Ollama model to tidy the transcript. A guard rejects any output that
   isn't recognisably your own words — a small model will otherwise happily *answer* a dictated
   question instead of transcribing it.
4. `paste.py` writes to the clipboard and sends ⌘V, then restores what was there before.

## Performance

Measured on an Apple Silicon Mac, `whisper-large-v3-turbo`, `llama3.2:3b`:

| | |
|---|---|
| Idle CPU | 0.0% |
| 9 s of speech, end to end | ~4 s |
| Whisper on 9 s of clean speech | ~1.2 s |
| Short, clean dictation (skips the LLM) | cleanup in < 1 ms |

Things that keep it light:

- **Whisper's retry is bounded, not removed.** Its temperature fallback re-decodes the same
  audio at rising temperatures when the output looks poor — up to six passes by default, which
  took one noisy clip to **113 s** and is what "the app hangs" feels like. It's capped at three.
  Removing it outright is a trap: that fallback is also the only thing that rescues a
  repetition loop, and without it every dictation came back as `The The The The…`.
  A token-level loop guard backs it up, and a word now counts once per dictation towards the
  learned dictionary, so a single looped transcript can't teach it junk.
- **The LLM is skipped** when a transcript is short, filler-free and already punctuated.
- **All UI on the main thread.** AppKit writes from background threads (menu titles, the
  status icon) could deadlock the app's view lock — it froze with the menu gone. Every rumps
  setter now routes through the main thread (`mainthread.py`).
- **No subprocesses on the hot path**: the pasteboard, the ⌘V keystroke, the frontmost-app
  lookup, the cues and the music check all use native APIs (the frontmost-app lookup alone went
  from ~430 ms of AppleScript to ~2 ms).
- **Whisper preloads** in the background at launch, so the first dictation isn't the slow one.
- **The window frees its web view** when you close it.
- A **watchdog** abandons a transcription that runs past `transcribe_timeout` (90 s).

To go lighter still, set `whisper.model` to a smaller MLX Whisper, `cleanup.enabled` to `false`,
or a shorter `cleanup.keep_alive` so Ollama releases its model sooner (it reloads in ~3 s).

## FAQ

**Does any audio leave my Mac?** No. There is no network call in the dictation path. Ollama and
Whisper both run locally.

**Does it work offline?** Yes, once the model is downloaded.

**Why is it Apple Silicon only?** It uses MLX for GPU inference, which is Apple-Silicon only.

**Is it as accurate as the paid apps?** It uses `whisper-large-v3-turbo`, which is strong. The
cleanup model is a 3B, so it's deliberately conservative — it tidies rather than rewrites.

**Can I use a different model?** Yes — any MLX Whisper repo, and any Ollama model you've pulled.

**Can I use it at work / in my company?** Not under this licence — see below.

## Licence

**PolyForm Noncommercial 1.0.0** — see [LICENSE.md](LICENSE.md).

Use it, modify it, share it, fork it, for anything noncommercial: personal use, study, hobby
projects, research, charities, schools. **You may not sell it, repackage it as a paid product,
or build a commercial service on it.**

To be straight about it: this is a **source-available** licence, not an OSI-approved open source
licence, because it restricts commercial use. Please don't call it "open source" in the OSI sense.
If you want a commercial licence, open an issue.

## Third-party

LocalFlow bundles nothing and copies no one's code. It depends on
[rumps](https://github.com/jaredks/rumps) (BSD),
[pynput](https://github.com/moses-palmer/pynput) (**LGPL-3.0**),
[sounddevice](https://github.com/spatialaudio/python-sounddevice) (MIT),
[NumPy](https://numpy.org) (BSD),
[mlx-whisper](https://github.com/ml-explore/mlx-examples) (MIT),
[PyObjC](https://pyobjc.readthedocs.io) (MIT) and
[py2app](https://github.com/ronaldoussoren/py2app) (MIT).
Each keeps its own licence; pynput is LGPL-3.0 and is used unmodified as a normal import, so you
are free to replace it.

Whisper models are downloaded from Hugging Face under their own licences.

**Wispr Flow and Superwhisper are trademarks of their respective owners.** LocalFlow is an
independent project, is not affiliated with or endorsed by either, and contains none of their
code or assets. They're named here only to describe what this is an alternative to.

## Contributing

Issues and PRs welcome. Please run the tests first — they're headless and need no microphone:

```bash
./.venv/bin/python test_click.py
./.venv/bin/python test_dictionary.py
./.venv/bin/python test_features.py
```

---

⭐ **Star the repo** if this replaced a subscription for you.
