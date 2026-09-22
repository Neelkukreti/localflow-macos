"""LocalFlow — a fully local, Wispr-Flow-style dictation menu-bar app.

Double-click the mouse wheel to start listening and click once to finish (or
hold the Fn key and speak). Audio is transcribed locally with MLX Whisper,
cleaned up by a local Ollama model, and pasted into the frontmost app.

Which cleanup it gets depends on where you're typing (voices.py), what you say
can expand into saved text (snippets.py), and a second hotkey rewrites text you
have selected instead of typing new text (commands.py). Nothing leaves the Mac.
"""

import os
import sys
import json
import time
import shutil
import fcntl
import threading
import subprocess

import numpy as np
import rumps
from pynput import keyboard

import cleanup
import commands
import languages
import media
import transcribe
import voices
import ax_context
import menubar_icon
import permissions
from recorder import Recorder, list_input_devices, default_input_name
from paste import insert, UTF8_ENV
from history import History
from dictionary import Dictionary
from snippets import Snippets
from scratchpad import Scratchpad
from hub import Hub
from flowbar import FlowBar
from fn_key import FnListener
from mouse_trigger import ClickTrigger, MiddleClickListener

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
EXAMPLE_CONFIG_PATH = os.path.join(HERE, "config.example.json")

# Menu-bar states. The logo is drawn and tinted per state (menubar_icon.py):
# idle is a template image so macOS inverts it for light/dark menu bars, the
# rest keep their colour so you can tell at a glance what the app is doing.
IDLE, RECORDING, HANDS_FREE, COMMAND, WORKING = (
    "idle", "recording", "hands_free", "command", "working")

# Map config key names -> pynput keys for the "hold" trigger.
KEY_MAP = {
    "alt_r": keyboard.Key.alt_r,
    "alt_l": keyboard.Key.alt_l,
    "cmd_r": keyboard.Key.cmd_r,
    "cmd_l": keyboard.Key.cmd_l,
    "ctrl_r": keyboard.Key.ctrl_r,
    "ctrl_l": keyboard.Key.ctrl_l,
    "f13": keyboard.Key.f13,
    "f14": keyboard.Key.f14,
    "f15": keyboard.Key.f15,
}

COMBO_MAP = {
    "cmd": {keyboard.Key.cmd, keyboard.Key.cmd_r, keyboard.Key.cmd_l},
    "shift": {keyboard.Key.shift, keyboard.Key.shift_r, keyboard.Key.shift_l},
    "ctrl": {keyboard.Key.ctrl, keyboard.Key.ctrl_r, keyboard.Key.ctrl_l},
    "alt": {keyboard.Key.alt, keyboard.Key.alt_r, keyboard.Key.alt_l},
}

# The three audible cues, and the sound each one plays.
CUES = {"start": "Tink", "stop": "Pop", "done": "Glass", "warn": "Submarine"}
CUE_VOLUME_KEYS = ("start", "stop", "done")
CUE_LABELS = {
    "start": ("Start", "When a dictation begins."),
    "stop": ("Finish", "When you stop talking and it starts transcribing."),
    "done": ("Pasted", "When the finished text lands."),
}

# Hold-to-talk keys you can pick in Settings. Fn is handled by its own Quartz
# tap (pynput reports Fn press AND release as a release); the rest go through
# pynput. "none" means the wheel is your only trigger.
HOLD_KEYS = {
    "none": "None — mouse wheel only",
    "fn": "Fn (🌐)",
    "alt_r": "Right Option",
    "alt_l": "Left Option",
    "cmd_r": "Right Command",
    "ctrl_r": "Right Control",
    "f13": "F13",
    "f14": "F14",
    "f15": "F15",
}

DEFAULT_SHORTCUTS = {
    "paste_last": "<cmd>+<alt>+v",
    "copy_last": "<cmd>+<alt>+c",
    "command_mode": "<cmd>+<alt>+j",
    "scratchpad": "<cmd>+<alt>+s",
}


def load_config():
    """Load config.json, seeding it from the shipped example on a fresh clone.

    config.json holds your own microphone, vocabulary and volumes, so it is
    gitignored — the example is what a new install starts from.
    """
    if not os.path.exists(CONFIG_PATH) and os.path.exists(EXAMPLE_CONFIG_PATH):
        shutil.copyfile(EXAMPLE_CONFIG_PATH, CONFIG_PATH)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


# Phrases Whisper invents on quiet audio (learned from YouTube captions).
WHISPER_SILENCE_PHRASES = {"thank you", "thanks for watching", "you", "bye", "thank you for watching"}


def rms(audio):
    return float(np.sqrt(np.mean(np.square(audio))))


def frontmost_app():
    try:
        out = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=1, env=UTF8_ENV,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def beep(name, volume=1.0):
    """Play a system sound. Volume is a 0-1 multiplier — the cues are meant to be
    felt, not heard across the room, so the default in config.json is low."""
    try:
        subprocess.Popen(
            ["afplay", "-v", f"{max(0.0, min(float(volume), 1.0)):.2f}",
             f"/System/Library/Sounds/{name}.aiff"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


class LocalFlowApp(rumps.App):
    def __init__(self):
        menubar_icon.prewarm()
        super().__init__("LocalFlow", icon=menubar_icon.icon_for(IDLE),
                         template=True, quit_button=None)
        self.cfg = load_config()
        audio_cfg = self.cfg["audio"]
        self.recorder = Recorder(
            samplerate=audio_cfg["samplerate"],
            channels=audio_cfg["channels"],
            max_seconds=audio_cfg["max_seconds"],
            device=audio_cfg.get("input_device"),
            preferred=audio_cfg.get("preferred_devices"),
        )
        self.is_recording = False
        self.busy = False
        self.hands_free = False
        self.mode = "dictate"       # or "command"
        self.click_trigger = None
        self._pressed = set()
        self._timers = []
        self._ducked = []
        self._tap_errors = set()
        self._meter_stop = None
        self._bar_drag_timer = None
        self._job = 0        # bumped per dictation; a stale job's result is dropped
        self._fn_down_at = 0.0
        self._fn_tap_timer = None
        self._watchdog = None
        self.last_text = ""
        self.history = History()
        self.dictionary = Dictionary()
        self.snippets = Snippets()
        self.scratchpad = Scratchpad()
        ui = self.cfg.setdefault("ui", {})
        self.hub = Hub(self._hub_action)
        self.flowbar = FlowBar(
            enabled=ui.get("flow_bar", True),
            always_visible=ui.get("flow_bar_always", False),
            position=ui.get("flow_bar_pos"),
            on_move=self._save_bar_position,
        )
        self._bar_save_timer = None
        self._bootstrap_dictionary()

        self.status_item = rumps.MenuItem("Idle")
        self.last_item = rumps.MenuItem("Last: (nothing yet)", callback=self.copy_last)
        self.voice_item = rumps.MenuItem("Voice: —")
        self.mic_menu = rumps.MenuItem("Microphone")
        self.dict_menu = rumps.MenuItem("Dictionary")
        self.snip_menu = rumps.MenuItem("Snippets")
        self.level_menu = rumps.MenuItem("Cleanup level")
        self.lang_menu = rumps.MenuItem("Language")
        self.menu = [
            self.status_item,
            self.voice_item,
            None,
            self.last_item,
            rumps.MenuItem("Open LocalFlow…", callback=self.open_hub),
            rumps.MenuItem("Stop / reset", callback=self.force_idle),
            rumps.MenuItem("Scratchpad", callback=self.toggle_scratchpad),
            None,
            self.mic_menu,
            self.dict_menu,
            self.snip_menu,
            self.level_menu,
            self.lang_menu,
            None,
            rumps.MenuItem("Toggle cleanup", callback=self.toggle_cleanup),
            rumps.MenuItem("Learn from past dictations", callback=self.toggle_context),
            rumps.MenuItem("Read the screen for context", callback=self.toggle_ax),
            rumps.MenuItem("Forget past dictations", callback=self.clear_history),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]
        self._sync_cleanup_label()
        self._sync_context_label()
        self._sync_ax_label()
        self._build_mic_menu()
        self._build_dict_menu()
        self._build_snip_menu()
        self._build_level_menu()
        self._build_lang_menu()
        self._start_listener()
        self._start_shortcuts()
        self._warn_if_ollama_down()
        _install_reopen_hook()
        if ui.get("open_hub_on_launch", True):
            self.hub.open()

    def _set_state(self, state):
        """Swap the menu-bar logo. Idle rides macOS's template inversion; the
        active states are coloured, so template mode has to be off for them."""
        self.flowbar.set_state(state)
        path = menubar_icon.icon_for(state)
        if path:
            try:
                self.set_icon(path, template=(state == IDLE))
                return
            except Exception:
                pass
        self.title = {"recording": "●", "hands_free": "○", "working": "…"}.get(state, "")

    def _tap_failed(self, which):
        """An event tap couldn't be created — almost always a missing grant."""
        self._tap_errors.add(which)
        rumps.notification(
            "LocalFlow", f"The {which} trigger isn't working",
            "Grant LocalFlow Input Monitoring and Accessibility in System Settings "
            "→ Privacy & Security, then relaunch.",
        )

    # ---------- sound ----------

    def _cue(self, kind):
        """Play one of the three cues at its own volume.

        Separate levels because they do different jobs: the start blip fires
        while you're still deciding to talk, the finish one lands when you've
        stopped and can actually hear it.
        """
        if not self.cfg.get("play_sounds", True):
            return
        beep(CUES.get(kind, "Tink"), self._cue_volume(kind))

    def _cue_volume(self, kind):
        vols = self.cfg.get("sound_volumes") or {}
        fallback = self.cfg.get("sound_volume", 0.4)  # pre-split config
        key = kind if kind in CUE_VOLUME_KEYS else "stop"
        return float(vols.get(key, fallback))

    # ---------- hotkey handling ----------

    def _trigger_setup(self):
        """(wheel on?, hold key) — reading the newer keys, falling back to the
        older `key`/`keep_fn` pair so an existing config keeps working."""
        trig = self.cfg.get("trigger", {})
        if "wheel" in trig or "hold_key" in trig:
            return bool(trig.get("wheel", True)), trig.get("hold_key", "fn")
        legacy = trig.get("key", "mouse_middle")
        if legacy in ("mouse_middle", "wheel"):
            return True, ("fn" if trig.get("keep_fn", True) else "none")
        return False, (legacy if legacy in HOLD_KEYS else "fn")

    def _start_listener(self):
        if self.cfg.get("trigger", {}).get("mode") == "toggle":
            self.combo = self.cfg["trigger"]["toggle_combo"]
            listener = keyboard.Listener(
                on_press=self._on_press_toggle, on_release=self._on_release_toggle
            )
            listener.daemon = True
            listener.start()
            return

        wheel, hold_key = self._trigger_setup()
        if wheel:
            self._start_click_listener()
        if hold_key == "fn":
            self._fn_listener = FnListener(
                on_down=self._on_hold_down, on_up=self._on_hold_up,
                on_other_key=self._on_hold_combo, on_error=self._tap_failed,
            )
            self._fn_listener.start()
        elif hold_key in KEY_MAP:
            self.hold_key = KEY_MAP[hold_key]
            listener = keyboard.Listener(
                on_press=self._on_press_hold, on_release=self._on_release_hold
            )
            listener.daemon = True
            listener.start()

    def _start_shortcuts(self):
        """Global hotkeys that don't record: re-paste, re-copy, Command Mode."""
        keys = {**DEFAULT_SHORTCUTS, **(self.cfg.get("shortcuts") or {})}
        binding = {
            keys.get("paste_last"): self._hk_paste_last,
            keys.get("copy_last"): self._hk_copy_last,
            keys.get("command_mode"): self._hk_command_mode,
            keys.get("scratchpad"): lambda: self.toggle_scratchpad(None),
        }
        binding = {k: v for k, v in binding.items() if k}
        if not binding:
            return
        try:
            hk = keyboard.GlobalHotKeys(binding)
            hk.daemon = True
            hk.start()
            self._hotkeys = hk
        except Exception as e:  # a bad combo in config must not kill the app
            rumps.notification("LocalFlow", "Shortcuts not registered", str(e))

    def _hk_paste_last(self):
        if self.last_text and not self.busy:
            insert(self.last_text, self.cfg["paste"])

    def _hk_copy_last(self):
        if self.last_text:
            subprocess.run(["pbcopy"], input=self.last_text, text=True, env=UTF8_ENV)
            self._cue("stop")

    def _hk_command_mode(self):
        """Press once to start listening for an instruction, again to run it."""
        if self.busy:
            return
        if self.is_recording:
            if self.mode == "command":
                self.stop_and_process()
            return
        self.mode = "command"
        self.start_recording()
        self.hands_free = True
        self._set_state(COMMAND)
        self.status_item.title = "Command… (say what to do, press again)"

    def _start_click_listener(self):
        trig = self.cfg["trigger"]

        def build(replay):  # the listener owns replaying a click it held back
            self.click_trigger = ClickTrigger(
                on_start=self._start_listening,
                on_stop=self.stop_and_process,
                is_active=lambda: not self.busy and not self.is_recording,
                replay=replay,
                double_seconds=trig.get("double_click_seconds", 0.35),
            )
            return self.click_trigger

        listener = MiddleClickListener(build, pass_through=trig.get("pass_through_clicks", True))
        listener.on_error = self._tap_failed
        listener.start()

    def _start_listening(self):
        """Double-click: record hands-free until the next click."""
        self.start_recording()
        self.hands_free = True
        self._set_state(HANDS_FREE)
        self.status_item.title = "Listening… (click the wheel to finish)"

    def _on_press_hold(self, key):
        if key == self.hold_key:
            self._on_hold_down()

    def _on_release_hold(self, key):
        if key == self.hold_key:
            self._on_hold_up()

    # The hold key has three gestures, matching Wispr Flow:
    #   hold                -> talk while held, stops on release
    #   tap, tap            -> hands free, keeps listening until the next tap
    #   tap (while locked)  -> finish
    def _fn_windows(self):
        trig = self.cfg.get("trigger", {})
        return (trig.get("hold_min_seconds", 0.35), trig.get("double_tap_seconds", 0.35))

    def _cancel_fn_tap_timer(self):
        if self._fn_tap_timer is not None:
            self._fn_tap_timer.cancel()
            self._fn_tap_timer = None

    def _on_hold_down(self):
        if self.busy:
            return
        if self.is_recording and self.hands_free:
            self._cancel_fn_tap_timer()
            self.stop_and_process()      # locked: this tap ends it
            return
        if self.is_recording and self._fn_tap_timer is not None:
            # Second tap inside the window: lock hands-free instead of stopping.
            self._cancel_fn_tap_timer()
            self.hands_free = True
            self._set_state(HANDS_FREE)
            self.status_item.title = "Listening… (tap again to finish)"
            return
        if not self.is_recording:
            self._fn_down_at = time.time()
            self.start_recording()

    def _on_hold_up(self):
        if not self.is_recording or self.hands_free:
            return
        hold_min, double_window = self._fn_windows()
        if time.time() - self._fn_down_at >= hold_min:
            self.stop_and_process()      # a genuine hold
            return

        # A short tap: wait to see whether a second one turns it hands-free.
        def settle():
            self._fn_tap_timer = None
            if self.is_recording and not self.hands_free:
                self.stop_and_process()
        self._cancel_fn_tap_timer()
        self._fn_tap_timer = threading.Timer(double_window, settle)
        self._fn_tap_timer.daemon = True
        self._fn_tap_timer.start()

    def _on_hold_combo(self):
        if self.is_recording:
            self._cancel_fn_tap_timer()
            self.cancel_recording()  # a shortcut like Fn+Delete, not dictation

    def _combo_satisfied(self):
        for name in self.combo:
            if name in COMBO_MAP:
                if not (self._pressed & COMBO_MAP[name]):
                    return False
            else:  # plain character like "space"
                want = getattr(keyboard.Key, name, None)
                if want is not None:
                    if want not in self._pressed:
                        return False
                elif keyboard.KeyCode.from_char(name) not in self._pressed:
                    return False
        return True

    def _on_press_toggle(self, key):
        self._pressed.add(key)
        if self._combo_satisfied() and not self.busy:
            if self.is_recording:
                self.stop_and_process()
            else:
                self.start_recording()

    def _on_release_toggle(self, key):
        self._pressed.discard(key)

    # ---------- recording pipeline ----------

    def start_recording(self):
        self.is_recording = True
        self._set_state(RECORDING if self.mode == "dictate" else COMMAND)
        self.status_item.title = "Recording… (release to transcribe)"
        self._cue("start")
        self.recorder.start()
        if self.recorder.fell_back:
            rumps.notification(
                "LocalFlow", f"{self.recorder.device} isn't plugged in",
                f"Recording from {self.recorder.active_device or 'the system default'} instead.",
            )
        self._duck()
        self._start_meter()
        self._arm_duration_timers()

    def force_idle(self, _=None):
        """Abort whatever is in flight and go back to idle.

        MLX Whisper and Ollama both run in a worker thread that can't be killed
        safely, so we bump the job counter instead: the orphan finishes into a
        result nobody reads, and its text is never pasted.
        """
        was_busy = self.busy or self.is_recording
        self._job += 1
        self._clear_watchdog()
        self._clear_timers()
        self._stop_meter()
        if self.is_recording:
            try:
                self.recorder.stop()
            except Exception:
                pass
        self.is_recording = False
        self.hands_free = False
        self.mode = "dictate"
        self.busy = False
        if self.click_trigger is not None:
            self.click_trigger.reset()
        self._unduck()
        self._set_state(IDLE)
        self.status_item.title = "Idle"
        if was_busy:
            rumps.notification("LocalFlow", "Stopped", "Dropped the dictation in progress.")

    def _arm_watchdog(self, job):
        """If transcription wedges, don't leave the app stuck on ⏳ forever."""
        self._clear_watchdog()
        limit = self.cfg.get("transcribe_timeout", 180)
        if not limit:
            return

        def fire():
            if self.busy and self._job == job:
                self.status_item.title = "Transcription timed out"
                rumps.notification(
                    "LocalFlow", "Transcription timed out",
                    f"Gave up after {int(limit)}s. Use Stop / reset if this keeps happening.",
                )
                self.force_idle()
        self._watchdog = threading.Timer(limit, fire)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _clear_watchdog(self):
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    def _save_bar_position(self, pos):
        """windowDidMove_ fires for every frame of a drag, so settle first and
        write config once the bar has come to rest."""
        if self._bar_save_timer is not None:
            self._bar_save_timer.cancel()

        def write():
            self.cfg.setdefault("ui", {})["flow_bar_pos"] = pos
            save_config(self.cfg)
        self._bar_save_timer = threading.Timer(0.6, write)
        self._bar_save_timer.daemon = True
        self._bar_save_timer.start()

    def _start_meter(self):
        """Feed the flow bar's level meter while we record, ~12 fps."""
        self._stop_meter()
        stop = threading.Event()
        self._meter_stop = stop

        def loop():
            while not stop.wait(0.08):
                if not self.is_recording:
                    break
                self.flowbar.set_level(self.recorder.level)
            self.flowbar.clear_level()
        threading.Thread(target=loop, daemon=True).start()

    def _stop_meter(self):
        if self._meter_stop is not None:
            self._meter_stop.set()
            self._meter_stop = None

    def _duck(self):
        """Pause music while we listen. Off the hot path: AppleScript is slow."""
        if not self.cfg.get("audio", {}).get("duck_other_audio", True):
            return

        def do():
            self._ducked = media.pause_players()
        threading.Thread(target=do, daemon=True).start()

    def _unduck(self):
        apps, self._ducked = self._ducked, []
        if apps:
            threading.Thread(target=media.resume, args=(apps,), daemon=True).start()

    def _arm_duration_timers(self):
        """Warn near the cap and stop at it, instead of silently dropping audio
        once the recorder's buffer is full."""
        self._clear_timers()
        cap = self.cfg["audio"]["max_seconds"]
        warn_at = max(5, cap - 30)

        def warn():
            if self.is_recording:
                self._cue("warn")
                self.status_item.title = f"{cap - warn_at:.0f}s left — finish up"

        def cut():
            if self.is_recording:
                self.stop_and_process()
        self._timers = [threading.Timer(warn_at, warn), threading.Timer(cap, cut)]
        for t in self._timers:
            t.daemon = True
            t.start()

    def _clear_timers(self):
        for t in self._timers:
            t.cancel()
        self._timers = []

    def cancel_recording(self):
        self.is_recording = False
        self.hands_free = False
        self.mode = "dictate"
        self._clear_timers()
        self._stop_meter()
        self._cancel_fn_tap_timer()
        if self.click_trigger is not None:
            self.click_trigger.reset()
        self.recorder.stop()
        self._unduck()
        self._set_state(IDLE)
        self.status_item.title = "Idle"

    def stop_and_process(self):
        mode, self.mode = self.mode, "dictate"
        self.is_recording = False
        self.hands_free = False
        self.busy = True
        self._clear_timers()
        self._stop_meter()
        self._cancel_fn_tap_timer()
        self._set_state(WORKING)
        self.status_item.title = "Transcribing…"
        self._cue("stop")
        audio = self.recorder.stop()
        self._unduck()
        self._job += 1
        job = self._job
        self._arm_watchdog(job)
        threading.Thread(target=self._process, args=(audio, mode, job), daemon=True).start()

    def _prompt_for(self, app_name, seed):
        """Whisper's initial_prompt: dictionary first (it must never be evicted),
        then the seed that fixes the output script, then what's on screen, then
        recent dictations. Whisper only reads the tail, so newest text goes last."""
        ctx_cfg = self.cfg.get("context", {})
        if not ctx_cfg.get("enabled", True):
            return ", ".join(self.dictionary.prompt_words()) or seed

        vocabulary = self.dictionary.prompt_words()
        words = ", ".join(vocabulary) + "." if vocabulary else ""
        screen = ""
        if ctx_cfg.get("read_screen", False):
            screen = ax_context.surrounding_text(ctx_cfg.get("screen_chars", 300))
        budget = max(0, 800 - len(words) - len(seed) - len(screen) - 3)
        recent = self.history.recent(ctx_cfg.get("history_chars", 1200), app_name)
        return " ".join(p for p in (words, seed, screen, " ".join(recent)[-budget:]) if p).strip()

    def _process(self, audio, mode="dictate", job=None):
        try:
            if audio.size < self.cfg["audio"]["samplerate"] * 0.3:
                return  # < 0.3s — ignore accidental taps
            if np.abs(audio).max() < 0.003:
                mic = self.recorder.active_device or "the selected mic"
                self.status_item.title = f"Heard silence on {mic}"
                rumps.notification("LocalFlow", "Heard silence",
                                   f"{mic} recorded nothing — pick another in Microphone.")
                return  # dead/muted input; Whisper would invent "Thank you."

            language, seed, romanize = languages.resolve(self.cfg["whisper"])
            app_name = frontmost_app()
            prompt = self._prompt_for(app_name, seed)
            whisper_cfg = {**self.cfg["whisper"], "language": language}
            raw = transcribe.transcribe(audio, whisper_cfg, prompt=prompt)
            if not raw.strip():
                return
            if raw.strip().lower().strip(".!") in WHISPER_SILENCE_PHRASES and rms(audio) < 0.01:
                return  # a quiet clip Whisper filled with a stock phrase
            if romanize:
                raw = languages.romanise(raw)
            if job is not None and job != self._job:
                return  # cancelled while Whisper was still running

            if mode == "command":
                self._run_command(raw)
                return

            voice_name, voice = voices.resolve(app_name, self.cfg)
            self.voice_item.title = f"Voice: {voice_name} ({app_name or 'unknown app'})"
            self.status_item.title = "Cleaning up…"
            final = cleanup.clean(raw, self.cfg["cleanup"],
                                  level=voice.get("level"), style=voice.get("style"))
            if not final.strip():
                final = raw
            final = self.dictionary.apply_fixes(final)
            final = self.snippets.expand(final)

            if self.cfg.get("context", {}).get("enabled", True):
                self.history.add(final, app_name)
                if self.dictionary.learn(final):
                    self._sync_dict_title()  # new words are waiting to be reviewed
            if job is not None and job != self._job:
                return  # cancelled during cleanup — don't paste into whatever is focused now
            self.last_text = final
            self.last_item.title = f"Last: {final[:40]}"
            self._deliver(final, voice)
            self.hub.refresh()
        except Exception as e:  # keep the app alive no matter what
            self.status_item.title = f"Error: {e}"
            rumps.notification("LocalFlow", "Error", str(e))
        finally:
            if job is None or job == self._job:
                self._clear_watchdog()
                self.busy = False
                self._set_state(IDLE)
            if not self.status_item.title.startswith(("Error", "Heard silence")):
                self.status_item.title = "Idle"

    def _deliver(self, text, voice):
        """Where the finished text goes: the scratchpad if it's open, otherwise
        the frontmost app — unless that's a password field, which we never type
        into (macOS blocks synthetic keystrokes there anyway)."""
        if self.scratchpad.is_open:
            self.scratchpad.append(text)
            self._cue("done")
            return
        if ax_context.is_secure_input() or ax_context.is_secret_field():
            subprocess.run(["pbcopy"], input=text, text=True, env=UTF8_ENV)
            rumps.notification("LocalFlow", "Not pasting into a password field",
                               "The text is on your clipboard instead.")
            return
        insert(text, self.cfg["paste"], press_enter=bool(voice.get("press_enter")))
        self._cue("done")

    def _run_command(self, instruction):
        """Command Mode: either a web search, or a rewrite of the selected text."""
        instruction = instruction.strip()
        if not instruction:
            return
        cmd_cfg = {**self.cfg["cleanup"], **(self.cfg.get("command") or {})}

        web = commands.parse_web(instruction)
        if web:
            url, engine, query = web
            commands.open_url(url)
            self.status_item.title = f"Searched {engine}"
            rumps.notification("LocalFlow", f"Asked {engine}", query)
            return

        selection = ax_context.selected_text()
        if not selection:
            rumps.notification("LocalFlow", "Nothing selected",
                               f"Select some text first, then say: {instruction[:50]}")
            return
        self.status_item.title = "Rewriting…"
        out = commands.transform(selection, instruction, cmd_cfg)
        if not out:
            rumps.notification("LocalFlow", "Command didn't return an edit",
                               "Your text was left alone.")
            return
        self.last_text = out
        self.last_item.title = f"Last: {out[:40]}"
        insert(out, self.cfg["paste"])  # replaces the selection
        self._cue("done")

    # ---------- menu actions ----------

    def copy_last(self, _):
        if self.last_text:
            subprocess.run(["pbcopy"], input=self.last_text, text=True, env=UTF8_ENV)
            rumps.notification("LocalFlow", "Copied last transcript", self.last_text[:80])

    def open_hub(self, _=None):
        self.hub.open()

    def toggle_scratchpad(self, _):
        opened = self.scratchpad.toggle()
        self.menu["Scratchpad"].state = 1 if opened else 0

    def _build_mic_menu(self, refresh=False):
        if self.mic_menu._menu is not None:
            self.mic_menu.clear()
        chosen = self.recorder.device
        names = list_input_devices(refresh=refresh)
        missing = bool(chosen) and chosen not in names
        default = rumps.MenuItem(
            f"System default ({default_input_name() or 'none'})", callback=self.pick_mic
        )
        default.device_name = None
        default.state = 1 if chosen is None else 0
        self.mic_menu.add(default)
        self.mic_menu.add(None)
        for name in names:
            item = rumps.MenuItem(name, callback=self.pick_mic)
            item.device_name = name
            item.state = 1 if name == chosen else 0
            self.mic_menu.add(item)
        if missing:
            # Keep the unplugged choice visible instead of silently losing it.
            item = rumps.MenuItem(f"{chosen} (not plugged in)", callback=self.pick_mic)
            item.device_name = chosen
            item.state = 1
            self.mic_menu.add(None)
            self.mic_menu.add(item)
        self.mic_menu.add(None)
        self.mic_menu.add(rumps.MenuItem("Use this order when unplugged…",
                                         callback=self.edit_preferred))
        self.mic_menu.add(rumps.MenuItem("Refresh list", callback=self.refresh_mics))
        self.mic_menu.title = "Microphone ⚠︎" if missing else "Microphone"

    def pick_mic(self, sender):
        if self.is_recording or self.busy:
            rumps.notification("LocalFlow", "Busy", "Change the mic after this dictation finishes.")
            return
        self.recorder.device = sender.device_name
        self.cfg["audio"]["input_device"] = sender.device_name
        save_config(self.cfg)
        self._build_mic_menu()

    def edit_preferred(self, _):
        current = ", ".join(self.cfg["audio"].get("preferred_devices") or [])
        resp = rumps.Window(
            title="Microphone fallback order",
            message="Comma-separated, best first. Used when your chosen mic is unplugged.",
            default_text=current, ok="Save", cancel="Cancel", dimensions=(320, 60),
        ).run()
        if not resp.clicked:
            return
        order = [n.strip() for n in resp.text.split(",") if n.strip()]
        self.cfg["audio"]["preferred_devices"] = order
        self.recorder.preferred = order
        save_config(self.cfg)
        self._build_mic_menu()

    def refresh_mics(self, _):
        if self.is_recording or self.busy:
            return
        self._build_mic_menu(refresh=True)

    # ---------- cleanup level / language ----------

    def _build_level_menu(self):
        if self.level_menu._menu is not None:
            self.level_menu.clear()
        current = self.cfg["cleanup"].get("level", cleanup.DEFAULT_LEVEL)
        for name in ("off", "light", "standard", "aggressive"):
            item = rumps.MenuItem(name.capitalize(), callback=self.pick_level)
            item.level = name
            item.state = 1 if name == current else 0
            self.level_menu.add(item)
        self.level_menu.add(None)
        self.level_menu.add(rumps.MenuItem("(per-app voices override this)"))

    def pick_level(self, sender):
        self.cfg["cleanup"]["level"] = sender.level
        save_config(self.cfg)
        self._build_level_menu()

    def _build_lang_menu(self):
        if self.lang_menu._menu is not None:
            self.lang_menu.clear()
        current = (self.cfg["whisper"].get("language_mode")
                   or ("en" if self.cfg["whisper"].get("language") == "en" else "auto"))
        labels = {"en": "English", "hi": "Hindi (Roman script)",
                  "hinglish": "Hinglish", "auto": "Auto-detect"}
        for name, label in labels.items():
            item = rumps.MenuItem(label, callback=self.pick_language)
            item.mode = name
            item.state = 1 if name == current else 0
            self.lang_menu.add(item)

    def pick_language(self, sender):
        self.cfg["whisper"]["language_mode"] = sender.mode
        save_config(self.cfg)
        self._build_lang_menu()

    # ---------- snippets ----------

    def _build_snip_menu(self):
        if self.snip_menu._menu is not None:
            self.snip_menu.clear()
        self.snip_menu.add(rumps.MenuItem("Add a snippet…", callback=self.add_snippet))
        self.snip_menu.add(None)
        rows = self.snippets.listing()
        if not rows:
            self.snip_menu.add(rumps.MenuItem("(none yet)"))
        for key, entry in rows:
            count = entry.get("count") or 0
            label = f"{entry.get('trigger', key)} · {count}" if count else entry.get("trigger", key)
            item = rumps.MenuItem(label, callback=self.toggle_snippet)
            item.snip_key = key
            item.state = 1 if entry.get("state", "on") == "on" else 0
            self.snip_menu.add(item)
        self.snip_menu.add(None)
        self.snip_menu.add(rumps.MenuItem("Delete a snippet…", callback=self.delete_snippet))

    def add_snippet(self, _):
        resp = rumps.Window(
            title="Add a snippet",
            message="What you say = what gets typed, e.g.   my booking link = https://cal.com/you",
            default_text="", ok="Save", cancel="Cancel", dimensions=(320, 80),
        ).run()
        if not resp.clicked or "=" not in resp.text:
            return
        trigger, _, text = resp.text.partition("=")
        if self.snippets.add(trigger, text.strip()):
            self._build_snip_menu()
            rumps.notification("LocalFlow", "Snippet saved", f"{trigger.strip()} → {text.strip()[:60]}")

    def toggle_snippet(self, sender):
        entry = self.snippets.items.get(sender.snip_key)
        if entry:
            self.snippets.set_state(sender.snip_key,
                                    "off" if entry.get("state", "on") == "on" else "on")
        self._build_snip_menu()

    def delete_snippet(self, _):
        resp = rumps.Window(
            title="Delete a snippet",
            message="Type the trigger phrase to delete.",
            default_text="", ok="Delete", cancel="Cancel", dimensions=(320, 24),
        ).run()
        if resp.clicked and self.snippets.remove(resp.text.strip().lower()):
            self._build_snip_menu()

    # ---------- dictionary ----------

    def _bootstrap_dictionary(self):
        """First run: adopt the old config vocabulary and mine past dictations."""
        self.dictionary.import_words(self.cfg.get("context", {}).get("vocabulary", []))
        if not self.dictionary.meta.get("seeded"):
            self.dictionary.seed(e["text"] for e in self.history.entries)

    def _build_dict_menu(self):
        if self.dict_menu._menu is not None:
            self.dict_menu.clear()
        pending = self.dictionary.unreviewed()
        self._sync_dict_title()
        review = rumps.MenuItem(
            f"Review new words ({len(pending)})…" if pending else "Review new words…",
            callback=self.review_words if pending else None,
        )
        self.dict_menu.add(review)
        self.dict_menu.add(rumps.MenuItem("Add a word…", callback=self.add_word))
        self.dict_menu.add(rumps.MenuItem("Fix a misheard word…", callback=self.add_fix))
        self.dict_menu.add(None)
        rows = self.dictionary.listing(limit=25)
        if not rows:
            self.dict_menu.add(rumps.MenuItem("(nothing yet — just talk)"))
        for key, e in rows:
            count = e.get("count") or 0
            item = rumps.MenuItem(f"{e['display']} · {count}" if count else e["display"],
                                  callback=self.toggle_word)
            item.word_key = key
            item.state = 1 if e["state"] == "on" else 0
            self.dict_menu.add(item)
        self.dict_menu.add(None)
        self.dict_menu.add(rumps.MenuItem("Forget learned words", callback=self.forget_words))

    def _sync_dict_title(self):
        n = len(self.dictionary.unreviewed())
        self.dict_menu.title = f"Dictionary ({n} new)" if n else "Dictionary"

    def toggle_word(self, sender):
        entry = self.dictionary.words.get(sender.word_key)
        if entry:
            self.dictionary.set_state(sender.word_key, "off" if entry["state"] == "on" else "on")
        self._build_dict_menu()

    def add_word(self, _):
        resp = rumps.Window(
            title="Add to dictionary",
            message="Names or terms LocalFlow should spell your way (comma-separated), e.g. Anthropic, Vercel",
            default_text="", ok="Add", cancel="Cancel", dimensions=(320, 24),
        ).run()
        words = [w.strip() for w in resp.text.split(",") if w.strip()] if resp.clicked else []
        for w in words:
            self.dictionary.add(w)
        if words:
            self._build_dict_menu()
            rumps.notification("LocalFlow", "Dictionary updated", ", ".join(words))

    def add_fix(self, _):
        resp = rumps.Window(
            title="Fix a misheard word",
            message="What you hear back = what it should say, e.g.   super base = Supabase",
            default_text="", ok="Save", cancel="Cancel", dimensions=(320, 24),
        ).run()
        if not resp.clicked or "=" not in resp.text:
            return
        heard, _, write = resp.text.partition("=")
        if self.dictionary.add_fix(heard, write):
            self._build_dict_menu()
            rumps.notification("LocalFlow", "Fix saved", f"{heard.strip()} → {write.strip()}")

    def review_words(self, _):
        pending = self.dictionary.unreviewed()
        if not pending:
            return
        resp = rumps.Window(
            title="Keep these words?",
            message="LocalFlow picked these up from your dictations. Delete any it should forget, then Keep.",
            default_text=", ".join(e["display"] for e in pending),
            ok="Keep", cancel="Cancel", dimensions=(320, 100),
        ).run()
        if not resp.clicked:
            return
        self.dictionary.review([w.strip() for w in resp.text.split(",") if w.strip()])
        self._build_dict_menu()

    def forget_words(self, _):
        self.dictionary.forget_learned()
        self._build_dict_menu()
        rumps.notification("LocalFlow", "Dictionary", "Forgot every word it learned on its own.")

    # ---------- toggles ----------

    def _sync_context_label(self):
        on = self.cfg.setdefault("context", {}).get("enabled", True)
        self.menu["Learn from past dictations"].state = 1 if on else 0

    def toggle_context(self, _):
        ctx = self.cfg.setdefault("context", {})
        ctx["enabled"] = not ctx.get("enabled", True)
        save_config(self.cfg)
        self._sync_context_label()

    def _sync_ax_label(self):
        on = self.cfg.setdefault("context", {}).get("read_screen", False)
        self.menu["Read the screen for context"].state = 1 if on else 0

    def toggle_ax(self, _):
        ctx = self.cfg.setdefault("context", {})
        ctx["read_screen"] = not ctx.get("read_screen", False)
        save_config(self.cfg)
        self._sync_ax_label()

    def clear_history(self, _):
        self.history.clear()
        rumps.notification("LocalFlow", "Forgot past dictations", "Context history cleared.")

    def _sync_cleanup_label(self):
        on = self.cfg["cleanup"].get("enabled", True)
        self.menu["Toggle cleanup"].state = 1 if on else 0

    def toggle_cleanup(self, _):
        self.cfg["cleanup"]["enabled"] = not self.cfg["cleanup"].get("enabled", True)
        self._sync_cleanup_label()

    # ---------- hub bridge ----------

    def _hub_action(self, action, payload):
        """Everything the window can ask for. Runs on the main thread."""
        fn = getattr(self, "_hub_" + action, None)
        if fn is None:
            return {"error": f"unknown action {action}"}
        return fn(payload) or {}

    def _hub_status(self, _payload):
        entries = self.history.entries
        words = sum(len(e.get("text", "").split()) for e in entries)
        mic = self.recorder.active_device or self.recorder.device or default_input_name()
        missing = bool(self.recorder.device) and self.recorder.device not in list_input_devices()
        note = (f"Falls back from {self.recorder.device}, which isn't plugged in."
                if missing else "Used for the next dictation.")
        for which in sorted(self._tap_errors):
            note = f"The {which} trigger failed to start. " + note
        return {
            "words": words,
            "count": len(entries),
            "wpm_minutes": round(words / 40) if words else 0,
            "perms": permissions.snapshot(),
            "ollama": cleanup.ollama_alive(self.cfg["cleanup"]),
            "model": self.cfg["cleanup"].get("model", "llama3.2:3b"),
            "whisper": self.cfg["whisper"].get("model", "").split("/")[-1],
            "mic": mic or "none",
            "mic_ok": not missing and not self._tap_errors,
            "mic_note": note,
            "memory": self._memory_mb(),
        }

    @staticmethod
    def _memory_mb():
        """Peak RSS of this process. Whisper's weights dominate it."""
        try:
            import resource
            return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024))
        except Exception:
            return 0

    def _hub_history(self, _payload):
        import datetime
        items = []
        for e in reversed(self.history.entries[-100:]):
            when = datetime.datetime.fromtimestamp(e.get("t", 0)).strftime("%d %b %H:%M")
            items.append({"text": e.get("text", ""), "app": e.get("app"), "when": when})
        return {"items": items}

    def _hub_copy(self, payload):
        items = list(reversed(self.history.entries[-100:]))
        i = int(payload.get("index", -1))
        if 0 <= i < len(items):
            subprocess.run(["pbcopy"], input=items[i].get("text", ""), text=True, env=UTF8_ENV)
        return {"ok": True}

    def _hub_dictionary(self, _payload):
        return {"items": [
            {"key": k, "display": e["display"], "count": e.get("count") or 0,
             "on": e.get("state") == "on"}
            for k, e in self.dictionary.listing(limit=300)
        ]}

    def _hub_toggle_word(self, payload):
        key = payload.get("key")
        entry = self.dictionary.words.get(key)
        if entry:
            self.dictionary.set_state(key, "off" if entry["state"] == "on" else "on")
            self._build_dict_menu()
        return {"ok": True}

    def _hub_add_words(self, payload):
        for w in [x.strip() for x in str(payload.get("text", "")).split(",") if x.strip()]:
            self.dictionary.add(w)
        self._build_dict_menu()
        return {"ok": True}

    def _hub_snippets(self, _payload):
        return {"items": [
            {"key": k, "trigger": e.get("trigger", k), "text": e.get("text", ""),
             "on": e.get("state", "on") == "on"}
            for k, e in self.snippets.listing()
        ]}

    def _hub_add_snippet(self, payload):
        self.snippets.add(payload.get("trigger", ""), payload.get("text", ""))
        self._build_snip_menu()
        return {"ok": True}

    def _hub_toggle_snippet(self, payload):
        key = payload.get("key")
        entry = self.snippets.items.get(key)
        if entry:
            self.snippets.set_state(key, "off" if entry.get("state", "on") == "on" else "on")
            self._build_snip_menu()
        return {"ok": True}

    def _hub_delete_snippet(self, payload):
        self.snippets.remove(payload.get("key"))
        self._build_snip_menu()
        return {"ok": True}

    def _hub_clear_history(self, _payload):
        self.history.clear()
        return {"ok": True}

    def _hub_settings(self, _payload):
        names = list_input_devices()
        chosen = self.recorder.device
        missing = chosen if chosen and chosen not in names else None
        labels = {"paste_last": "Paste last dictation", "copy_last": "Copy last dictation",
                  "command_mode": "Command Mode", "scratchpad": "Scratchpad"}
        keys = {**DEFAULT_SHORTCUTS, **(self.cfg.get("shortcuts") or {})}
        pretty = {"<cmd>": "⌘", "<alt>": "⌥", "<ctrl>": "⌃", "<shift>": "⇧", "+": ""}
        out = []
        for k, label in labels.items():
            combo = keys.get(k, "")
            for a, b in pretty.items():
                combo = combo.replace(a, b)
            out.append({"label": label, "combo": combo.upper()})
        out.append({"label": "Dictate", "combo": "FN  ·  WHEEL ×2"})
        return {
            "cues": [{"key": k, "label": CUE_LABELS[k][0], "note": CUE_LABELS[k][1],
                      "value": self._cue_volume(k)} for k in CUE_VOLUME_KEYS],
            "play_sounds": bool(self.cfg.get("play_sounds", True)),
            "language": self.cfg["whisper"].get("language_mode", "en"),
            "level": self.cfg["cleanup"].get("level", cleanup.DEFAULT_LEVEL),
            "mics": names, "mic": chosen or "", "mic_missing": missing,
            "mic_note": (f"{missing} isn't plugged in — falling back."
                         if missing else "Falls back automatically when unplugged."),
            "keys": out,
            "trigger": {
                "hold_key": self._trigger_setup()[1],
                "wheel": self._trigger_setup()[0],
                "options": [{"value": k, "label": v} for k, v in HOLD_KEYS.items()],
                "pass_through": bool(self.cfg.get("trigger", {}).get("pass_through_clicks", True)),
                "hold_min": float(self.cfg.get("trigger", {}).get("hold_min_seconds", 0.35)),
                "double_tap": float(self.cfg.get("trigger", {}).get("double_tap_seconds", 0.35)),
            },
            "toggles": [
                {"key": "read_screen", "label": "Use screen context",
                 "note": "Reads the text around your cursor so a reply matches its thread. "
                         "Needs Accessibility; never reads password fields.",
                 "on": bool(self.cfg.get("context", {}).get("read_screen", False))},
                {"key": "context_enabled", "label": "Learn from past dictations",
                 "note": "Feeds your recent phrasing to Whisper so names stay consistent.",
                 "on": bool(self.cfg.get("context", {}).get("enabled", True))},
                {"key": "duck_audio", "label": "Pause music while listening",
                 "note": "Pauses Spotify or Music for the length of a dictation.",
                 "on": bool(self.cfg.get("audio", {}).get("duck_other_audio", True))},
                {"key": "flow_bar_always", "label": "Always show the floating bar",
                 "note": "Off means it only appears while you're dictating. Drag it anywhere.",
                 "on": bool(self.cfg.get("ui", {}).get("flow_bar_always", False))},
            ],
        }

    def _hub_set(self, payload):
        key, value = payload.get("key"), payload.get("value")
        if key == "cue_volume":
            cue = payload.get("cue")
            if cue not in CUE_VOLUME_KEYS:
                return {"error": "unknown cue"}
            self.cfg.setdefault("sound_volumes", {})[cue] = float(value)
        elif key == "play_sounds":
            self.cfg["play_sounds"] = bool(value)
        elif key == "read_screen":
            self.cfg.setdefault("context", {})["read_screen"] = bool(value)
            self._sync_ax_label()
        elif key == "context_enabled":
            self.cfg.setdefault("context", {})["enabled"] = bool(value)
            self._sync_context_label()
        elif key == "duck_audio":
            self.cfg.setdefault("audio", {})["duck_other_audio"] = bool(value)
        elif key == "trigger_hold_key":
            if value not in HOLD_KEYS:
                return {"error": "unknown key"}
            self.cfg.setdefault("trigger", {})["hold_key"] = value
        elif key == "trigger_wheel":
            self.cfg.setdefault("trigger", {})["wheel"] = bool(value)
        elif key == "pass_through_clicks":
            self.cfg.setdefault("trigger", {})["pass_through_clicks"] = bool(value)
        elif key in ("hold_min_seconds", "double_tap_seconds"):
            self.cfg.setdefault("trigger", {})[key] = float(value)
        elif key == "flow_bar_always":
            self.cfg.setdefault("ui", {})["flow_bar_always"] = bool(value)
            self.flowbar.always_visible = bool(value)
            self.flowbar.set_state("idle" if not self.is_recording else "recording")
        elif key == "language_mode":
            self.cfg["whisper"]["language_mode"] = value
            self._build_lang_menu()
        elif key == "level":
            self.cfg["cleanup"]["level"] = value
            self._build_level_menu()
        elif key == "mic":
            self.recorder.device = value or None
            self.cfg["audio"]["input_device"] = value or None
            self._build_mic_menu()
        else:
            return {"error": "unknown key"}
        save_config(self.cfg)
        return {"ok": True}

    def _hub_test_sound(self, payload):
        cue = payload.get("cue", "start")
        beep(CUES.get(cue, "Tink"), float(payload.get("value", self._cue_volume(cue))))
        return {"ok": True}

    def _hub_move_bar(self, payload):
        """Make the floating bar draggable for a moment, then click-through again."""
        seconds = float(payload.get("seconds", 20))
        self.flowbar.set_draggable(True)
        self.flowbar.set_state(self._bar_state_now())
        if getattr(self, "_bar_drag_timer", None) is not None:
            self._bar_drag_timer.cancel()
        t = threading.Timer(seconds, lambda: self.flowbar.set_draggable(False))
        t.daemon = True
        t.start()
        self._bar_drag_timer = t
        return {"ok": True, "seconds": seconds}

    def _bar_state_now(self):
        if not self.is_recording:
            return "idle"
        return "command" if self.mode == "command" else (
            HANDS_FREE if self.hands_free else RECORDING)

    def _hub_relaunch(self, _payload):
        """Restart so new trigger settings take hold.

        The Quartz event taps are created once at launch and run their own run
        loops, so switching the hold key means starting over. Relaunch is handed
        to a detached shell that waits for this process to release its lock.
        """
        try:
            subprocess.Popen(
                ["/bin/sh", "-c", "sleep 1.5; open -a LocalFlow"],
                start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            return {"error": str(e)}
        rumps.Timer(lambda _t: rumps.quit_application(), 0.4).start()
        return {"ok": True}

    def _hub_open_pane(self, payload):
        permissions.open_pane(payload.get("which"))
        return {"ok": True}

    def _warn_if_ollama_down(self):
        """Start Ollama if it isn't up. run.sh used to do this, but launching from
        Spotlight goes straight to this process, so the app has to do it itself."""
        if not self.cfg["cleanup"].get("enabled", True):
            return
        if cleanup.ollama_alive(self.cfg["cleanup"]):
            return

        def boot():
            try:
                subprocess.Popen(["ollama", "serve"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, ValueError):
                pass
            # Give it a moment, then say so only if it really didn't come up.
            for _ in range(10):
                time.sleep(1)
                if cleanup.ollama_alive(self.cfg["cleanup"]):
                    return
            rumps.notification(
                "LocalFlow",
                "Ollama not reachable",
                "Cleanup will fall back to rule-based. Run: ollama serve",
            )
        threading.Thread(target=boot, daemon=True).start()

    def quit_app(self, _):
        self._clear_timers()
        self._clear_watchdog()
        self.scratchpad.close()
        rumps.quit_application()


def _install_reopen_hook():
    """Re-launching from Spotlight should show the window.

    macOS activates the running instance instead of starting a second one, so
    without this nothing visible happens and the app looks broken. The hook
    lives on rumps' own NSApplication delegate.
    """
    try:
        import objc
        from rumps.rumps import NSApp as RumpsNSApp

        if hasattr(RumpsNSApp, "applicationShouldHandleReopen_hasVisibleWindows_"):
            return

        def applicationShouldHandleReopen_hasVisibleWindows_(self, _sender, _flag):
            app = getattr(rumps.App, "*app_instance", None)
            if app is not None and getattr(app, "hub", None) is not None:
                app.hub.open()
            return True

        objc.classAddMethods(RumpsNSApp, [applicationShouldHandleReopen_hasVisibleWindows_])
    except Exception:
        pass  # worst case: the menu item still opens the window


def single_instance():
    """Hold an exclusive lock for the life of the process.

    Launching from Spotlight while run.sh already has one going would put two
    suppressing event taps on the same click, which doubles every dictation.
    The handle is deliberately leaked: it must stay open until we exit.
    """
    handle = open(os.path.join(HERE, ".localflow.lock"), "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


if __name__ == "__main__":
    _lock = single_instance()
    if _lock is None:
        subprocess.run([
            "osascript", "-e",
            'display notification "LocalFlow is already running — look in the menu bar."'
            ' with title "LocalFlow"',
        ])
        sys.exit(0)
    LocalFlowApp().run()
