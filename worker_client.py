"""Talks to whisper_worker.py; kills and restarts it when it hangs."""

import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "whisper_worker.py")
VENV_PY = os.path.join(HERE, ".venv", "bin", "python")

# The first request after a (re)start also waits for the model to load.
LOAD_ALLOWANCE = 45.0


class WorkerTimeout(Exception):
    pass


class WorkerFailed(Exception):
    pass


class WorkerClient:
    def __init__(self, whisper_cfg):
        self.cfg = whisper_cfg
        self._proc = None
        self._lock = threading.Lock()          # one request at a time
        self._cv = threading.Condition()
        self._replies = {}
        self._ready = False
        self._next_id = 0

    # ---------- process ----------

    def start(self):
        """Spawn the worker if it isn't running. Returns immediately."""
        with self._cv:
            if self._proc is not None and self._proc.poll() is None:
                return
            python = VENV_PY if os.path.exists(VENV_PY) else sys.executable
            self._ready = False
            self._replies = {}
            self._proc = subprocess.Popen(
                [python, WORKER, json.dumps(self.cfg)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
            )
            proc = self._proc
        threading.Thread(target=self._read, args=(proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with self._cv:
                if proc is not self._proc:
                    return                     # a killed worker's late reply
                if msg.get("ready"):
                    self._ready = True
                elif "id" in msg:
                    self._replies[msg["id"]] = msg
                self._cv.notify_all()
        with self._cv:
            self._cv.notify_all()              # it died: wake any waiter

    def kill(self):
        """Stop the worker now, whatever it's doing."""
        with self._cv:
            proc, self._proc, self._ready = self._proc, None, False
            self._cv.notify_all()
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    def restart(self):
        self.kill()
        self.start()

    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    # ---------- requests ----------

    def request(self, payload, timeout):
        """Send one request; raise WorkerTimeout (after killing the worker) if
        it doesn't answer in time, WorkerFailed if it died or errored."""
        with self._lock:
            self.start()
            with self._cv:
                proc = self._proc
                self._next_id += 1
                rid = self._next_id
                budget = timeout + (0 if self._ready else LOAD_ALLOWANCE)
            try:
                proc.stdin.write(json.dumps({"id": rid, **payload}) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as e:
                self.kill()
                raise WorkerFailed(f"worker unavailable: {e}")
            with self._cv:
                done = self._cv.wait_for(
                    lambda: rid in self._replies or proc is not self._proc
                    or proc.poll() is not None,
                    timeout=budget)
                msg = self._replies.pop(rid, None)
            if msg is None:
                if not done:
                    self.kill()
                    raise WorkerTimeout(f"no answer in {budget:.0f}s")
                self.kill()
                raise WorkerFailed("worker stopped (killed, or it crashed)")
            if not msg.get("ok"):
                raise WorkerFailed(msg.get("error") or "unknown error")
            return msg.get("text", "")

    def transcribe(self, wav_path, cfg, prompt, timeout):
        return self.request({"wav": wav_path, "cfg": cfg, "prompt": prompt}, timeout)
