"""Microphone capture via sounddevice. Records into an in-memory buffer
between start() and stop(); returns float32 mono PCM at the configured rate.
"""

import threading
import numpy as np
import sounddevice as sd


def list_input_devices(refresh=False):
    """Names of every device with at least one input channel."""
    if refresh:  # PortAudio caches the device list until re-initialised
        sd._terminate()
        sd._initialize()
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]


def default_input_name():
    try:
        return sd.query_devices(kind="input")["name"]
    except Exception:
        return None


class Recorder:
    def __init__(self, samplerate=16000, channels=1, max_seconds=120, device=None,
                 preferred=None):
        self.samplerate = samplerate
        self.channels = channels
        self.max_seconds = max_seconds
        self.device = device  # device name, or None for the system default
        # Ordered fallbacks tried when `device` is unplugged, so an unplugged
        # USB mic drops to the built-in one instead of to whatever macOS has
        # made default (which may be a virtual device that records silence).
        self.preferred = list(preferred or [])
        self.active_device = None   # name actually opened, set by start()
        self.fell_back = False      # True when `device` was asked for but missing
        self._frames = []
        self._stream = None
        self._lock = threading.Lock()
        self._count = 0
        self._max_frames = 0
        self._stream_rate = samplerate
        self._peak = 0.0   # live input level, for the flow bar's meter

    def _callback(self, indata, frames, time_info, status):
        try:
            peak = float(np.abs(indata).max())
            # Decay slowly so the meter reads like a VU needle, not a strobe.
            self._peak = peak if peak > self._peak else self._peak * 0.80 + peak * 0.20
        except (ValueError, TypeError):
            pass
        with self._lock:
            if self._count >= self._max_frames:
                return
            self._frames.append(indata.copy())
            self._count += frames

    def _index_of(self, name):
        if not name:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == name and d["max_input_channels"] > 0:
                return i
        return None

    def _resolve_device(self):
        """(index, name) to record from, walking the preference list.

        Order: the chosen device, then each preferred fallback, then the system
        default. Sets `fell_back` so the app can say which mic it ended up on.
        """
        idx = self._index_of(self.device)
        if idx is not None:
            return idx, self.device
        for name in self.preferred:
            if name == self.device:
                continue
            idx = self._index_of(name)
            if idx is not None:
                return idx, name
        return None, default_input_name()

    def _open(self, device, rate):
        self._stream_rate = rate
        self._max_frames = int(rate * self.max_seconds)
        return sd.InputStream(
            device=device,
            samplerate=rate,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
        )

    def start(self):
        with self._lock:
            self._frames = []
            self._count = 0
        self._peak = 0.0
        device, name = self._resolve_device()
        self.active_device = name
        self.fell_back = bool(self.device) and name != self.device
        try:
            self._stream = self._open(device, self.samplerate)
        except sd.PortAudioError:
            # Some interfaces refuse 16 kHz; record at their native rate and resample.
            native = int(sd.query_devices(device, kind="input")["default_samplerate"])
            self._stream = self._open(device, native)
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._frames, axis=0)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if self._stream_rate != self.samplerate:
            n = int(len(audio) * self.samplerate / self._stream_rate)
            audio = np.interp(
                np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio
            )
        return audio.astype(np.float32)

    @property
    def level(self) -> float:
        """Most recent input peak, 0-1. 0 while not recording."""
        return 0.0 if self._stream is None else min(1.0, self._peak)

    @property
    def seconds(self) -> float:
        return self._count / self._stream_rate
