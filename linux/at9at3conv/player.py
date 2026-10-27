"""AT9/AT3 playback (replaces At9Player.cs/NAudio and MiniPlayer.exe).

The file is decoded to a temporary WAV (ffmpeg, or the Sony tool via Wine as
a fallback) on a worker thread, then played with the first available system
player: pw-play (PipeWire), paplay (PulseAudio), aplay (ALSA) or ffplay.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QThread, Signal

from .core import ConversionError, Toolchain, cache_dir, decode_for_playback, detect_input_kind

_PLAYERS = [
    ("pw-play", []),
    ("paplay", []),
    ("aplay", ["-q"]),
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
]


def find_player() -> tuple[str, list[str]] | None:
    for name, args in _PLAYERS:
        path = shutil.which(name)
        if path:
            return path, args
    return None


class _DecodeThread(QThread):
    # (generation, workdir, wav) / (generation, workdir, key, detail)
    decoded = Signal(int, str, str)
    failed = Signal(int, str, str, str)

    def __init__(self, generation: int, path: Path, tools: Toolchain, workdir: Path):
        super().__init__()
        self.generation, self.path, self.tools, self.workdir = generation, path, tools, workdir

    def run(self) -> None:
        g, w = self.generation, str(self.workdir)
        try:
            self.decoded.emit(g, w, str(decode_for_playback(self.path, self.tools, self.workdir)))
        except ConversionError as exc:
            self.failed.emit(g, w, exc.key, exc.detail)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(g, w, "playback_error", str(exc))


class AudioPlayer(QObject):
    preparing = Signal()
    started = Signal(str)          # "at9" or "at3"
    stopped = Signal()
    error = Signal(str, str)       # translation key, detail

    def __init__(self, tools: Toolchain, parent: QObject | None = None):
        super().__init__(parent)
        self.tools = tools
        self.process: QProcess | None = None
        self.decoder: _DecodeThread | None = None
        self.workdir: Path | None = None
        self.kind = ""
        self._generation = 0
        self._threads: set[_DecodeThread] = set()  # keep running threads alive

    def is_active(self) -> bool:
        return self.decoder is not None or self.process is not None

    def play(self, path: Path) -> None:
        self.stop(emit=False)
        if not path.is_file():
            self.error.emit("file_not_found", str(path))
            return
        if find_player() is None:
            self.error.emit("no_audio_player", "")
            return
        self.kind = detect_input_kind(path)
        cache_dir().mkdir(parents=True, exist_ok=True)
        self.workdir = Path(tempfile.mkdtemp(prefix="play-", dir=str(cache_dir())))
        self._generation += 1
        generation = self._generation
        workdir = self.workdir

        # Bound methods (not lambdas) so PySide queues these onto the GUI thread.
        thread = _DecodeThread(generation, path, self.tools, workdir)
        thread.decoded.connect(self._on_decoded)
        thread.failed.connect(self._on_failed)
        thread.finished.connect(self._reap_threads)
        self._threads.add(thread)
        self.decoder = thread
        self.preparing.emit()
        thread.start()

    def _reap_threads(self) -> None:
        self._threads = {t for t in self._threads if not t.isFinished()}

    def _on_decoded(self, generation: int, workdir: str, wav: str) -> None:
        if generation != self._generation:  # stopped while decoding
            shutil.rmtree(workdir, ignore_errors=True)
            return
        self.decoder = None
        player = find_player()
        if player is None:
            self.error.emit("no_audio_player", "")
            self._cleanup()
            return
        program, args = player
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(self._on_process_done)  # QProcess lives on the GUI thread
        proc.errorOccurred.connect(self._on_process_done)
        self.process = proc
        proc.start(program, args + [wav])
        self.started.emit(self.kind)

    def _on_failed(self, generation: int, workdir: str, key: str, detail: str) -> None:
        if generation != self._generation:
            shutil.rmtree(workdir, ignore_errors=True)
            return
        self.decoder = None
        self._cleanup()
        self.error.emit(key, detail)
        self.stopped.emit()

    def _on_process_done(self, *_args) -> None:
        # stop() detaches the old process before killing it, so anything that
        # arrives here belongs to the current one.
        proc = self.process
        if proc is None or proc.state() != QProcess.ProcessState.NotRunning:
            return  # nothing playing, or an error signal while still running
        proc.deleteLater()
        self.process = None
        self._cleanup()
        self.stopped.emit()

    def stop(self, emit: bool = True) -> None:
        self._generation += 1  # ignore results from anything still running
        if self.decoder is not None:
            # Let it finish on its own; its handler sees a stale generation
            # and removes its work dir.
            self.decoder = None
            self.workdir = None
        if self.process is not None:
            proc = self.process
            self.process = None
            proc.kill()
            proc.waitForFinished(2000)
            proc.deleteLater()
        self._cleanup()
        if emit:
            self.stopped.emit()

    def _cleanup(self) -> None:
        if self.workdir is not None:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None

    def shutdown(self) -> None:
        """Called when the window closes."""
        self.stop(emit=False)
        for thread in list(self._threads):
            thread.wait(10000)
