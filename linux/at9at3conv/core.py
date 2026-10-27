"""Conversion core: no Qt imports, so it can be used by the GUI and the CLI.

Mirrors ConversionModels.cs / ConversionService.cs / At9Controller.cs /
At3Controller.cs from the Windows version. All codecs run natively on Linux:
ATRAC9 uses the bundled C++ encoder, ATRAC3/3+ uses atracdenc, and FFmpeg
handles PCM conversion, decoding, and resampling. No Wine or Sony tools.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shlex
import shutil
import struct
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field, fields
from enum import Enum, IntEnum
from pathlib import Path
from typing import Callable, Optional

from . import APP_ID

StatusCallback = Callable[[str], None]


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------

def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    base = Path(value) if value else Path.home() / fallback
    return base / APP_ID


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def find_assets_dir() -> Path:
    """Directory holding ATRAC/, Resources/ and lang/.

    Order: $AT9AT3_ASSETS, an installed copy next to the package, the
    Windows project folder when running from a git checkout, then /usr/share.
    """
    pkg = Path(__file__).resolve().parent
    candidates = []
    if os.environ.get("AT9AT3_ASSETS"):
        candidates.append(Path(os.environ["AT9AT3_ASSETS"]).expanduser())
    candidates += [
        pkg.parent / "assets",
        pkg.parent.parent / "at3-at9_Converter",
        Path("/usr/lib") / APP_ID / "assets",
    ]
    for candidate in candidates:
        if (candidate / "Resources").is_dir():
            return candidate
    return candidates[0]


# --------------------------------------------------------------------------
# Models (ConversionModels.cs)
# --------------------------------------------------------------------------

class ConversionMode(Enum):
    NONE = ""
    WAV_TO_AT9 = "Wav -> AT9"
    WAV_TO_AT3 = "Wav -> AT3"
    MP3_TO_AT9 = "MP3 -> AT9"
    MP3_TO_AT3 = "MP3 -> AT3"
    AT9_TO_WAV = "AT9 -> Wav"
    AT9_TO_MP3 = "AT9 -> MP3"
    AT3_TO_WAV = "AT3 -> Wav"
    AT3_TO_MP3 = "AT3 -> MP3"

    @property
    def display_text(self) -> str:
        return self.value

    @property
    def is_at9(self) -> bool:
        return self in (ConversionMode.WAV_TO_AT9, ConversionMode.MP3_TO_AT9,
                        ConversionMode.AT9_TO_WAV, ConversionMode.AT9_TO_MP3)

    @property
    def is_at3(self) -> bool:
        return self in (ConversionMode.WAV_TO_AT3, ConversionMode.MP3_TO_AT3,
                        ConversionMode.AT3_TO_WAV, ConversionMode.AT3_TO_MP3)

    @property
    def is_encode(self) -> bool:
        return self in (ConversionMode.WAV_TO_AT9, ConversionMode.WAV_TO_AT3,
                        ConversionMode.MP3_TO_AT9, ConversionMode.MP3_TO_AT3)

    @property
    def is_decode(self) -> bool:
        return self in (ConversionMode.AT9_TO_WAV, ConversionMode.AT9_TO_MP3,
                        ConversionMode.AT3_TO_WAV, ConversionMode.AT3_TO_MP3)

    @property
    def needs_bitrate(self) -> bool:
        return self.is_encode

    @property
    def from_mp3(self) -> bool:
        return self in (ConversionMode.MP3_TO_AT9, ConversionMode.MP3_TO_AT3)

    @property
    def to_mp3(self) -> bool:
        return self in (ConversionMode.AT9_TO_MP3, ConversionMode.AT3_TO_MP3)

    @property
    def uses_intermediate_wav(self) -> bool:
        return self.from_mp3 or self.to_mp3

    @property
    def codec_ext(self) -> str:
        return ".at9" if self.is_at9 else ".at3"


class ToolOptionLevel(IntEnum):
    BASIC = 0
    ADVANCED = 1
    EXPERT = 2


class ToolLoopMode(IntEnum):
    WHOLE_LOOP = 0
    NO_LOOP = 1
    CUSTOM_LOOP = 2
    DEFAULT_WHOLE_LOOP = 3


class ToolPcmOutputFormat(IntEnum):
    INT16 = 0
    INT24 = 1
    FLOAT = 2


@dataclass
class ToolSettings:
    level: ToolOptionLevel = ToolOptionLevel.BASIC
    loop_mode: ToolLoopMode = ToolLoopMode.WHOLE_LOOP
    loop_start: int = 0
    loop_end: int = 0
    decode_repeat: int = 1
    use_sampling_rate: bool = False
    sampling_rate: int = 48000
    use_loop_list: bool = False
    loop_list_path: str = ""
    superframe_mode: int = 0  # 0 default, 1 on, 2 off
    dual_mode: bool = False
    use_quantized_bands: bool = False
    quantized_bands: int = 8
    use_intensity_band: bool = False
    intensity_band: int = -1
    use_gradient_mode: bool = False
    gradient_mode: int = 4
    wide_band: bool = False
    band_extension: bool = False
    lfe_super_low_cut: bool = False
    wave_extensible_header: bool = False
    pcm_output_format: ToolPcmOutputFormat = ToolPcmOutputFormat.INT16
    custom_encode_args: str = ""
    custom_decode_args: str = ""

    def clone(self) -> "ToolSettings":
        return ToolSettings(**{f.name: getattr(self, f.name) for f in fields(self)})


AT9_CONSOLES = ["PS4", "PSVita"]
AT3_CONSOLES = ["PSP", "PS3"]

BITRATES = {
    "PSP": ["66", "105", "132", "146", "176", "265", "352"],
    "PS3": ["64", "96", "128", "192", "256", "320", "352"],
    "PSVita": ["72", "96", "120", "144", "168", "192"],
    "PS4": ["72", "96", "120", "144", "168", "192"],
}


def consoles_for(mode: ConversionMode) -> list[str]:
    if mode.is_at9:
        return list(AT9_CONSOLES)
    if mode.is_at3:
        return list(AT3_CONSOLES)
    return []


def target_sample_rate(console: str) -> int:
    # The native ATRAC3/3+ backend currently supports 44.1 kHz PCM. ATRAC9
    # operates at 48 kHz. Both are accepted by the target consoles.
    return 44100 if console in AT3_CONSOLES else 48000


def validate_settings(s: ToolSettings, console: str, mode: ConversionMode) -> str:
    """Returns a translation key, or "" when valid (ToolOptionsDialog.ValidateSettings)."""
    if s.level == ToolOptionLevel.BASIC:
        return ""
    if mode.is_encode and s.loop_mode == ToolLoopMode.CUSTOM_LOOP:
        if s.loop_end <= s.loop_start:
            return "custom_loop_end_error"
        if console in ("PSP", "PS3") and s.loop_end < s.loop_start + 6143:
            return "at3_custom_loop_samples_error"
    if s.level != ToolOptionLevel.EXPERT:
        return ""
    if s.band_extension and s.wide_band:
        return "ps4_bex_wband_error"
    if s.band_extension and s.use_sampling_rate and s.sampling_rate != 48000:
        return "ps4_bex_sample_rate_error"
    if s.band_extension and s.use_quantized_bands and not 5 <= s.quantized_bands <= 10:
        return "ps4_bex_nbands_error"
    if s.use_loop_list and not s.loop_list_path.strip():
        return "loop_list_empty_error"
    if s.use_loop_list and not Path(s.loop_list_path).expanduser().is_file():
        return "loop_list_missing_error"
    return ""


# --------------------------------------------------------------------------
# Jobs and results
# --------------------------------------------------------------------------

def detect_input_kind(path: Path) -> str:
    """'wav', 'mp3', 'at9', 'at3' or '' for unsupported."""
    ext = path.suffix.lower()
    return {".wav": "wav", ".mp3": "mp3", ".at9": "at9", ".at3": "at3"}.get(ext, "")


@dataclass
class ConversionJob:
    mode: ConversionMode
    input_file: Path
    console: str
    bitrate: str = ""
    settings: ToolSettings = field(default_factory=ToolSettings)

    @property
    def tool_name(self) -> str:
        return "atrac9-encoder" if self.mode.is_at9 else "atracdenc"

    @property
    def is_ps4(self) -> bool:
        return self.console == "PS4"

    @property
    def intermediate_wav(self) -> Path:
        return self.input_file.with_suffix(".wav")

    @property
    def final_file(self) -> Path:
        """Same naming as the Windows controllers."""
        if self.mode.is_encode:
            stem = self.input_file.stem
            if self.bitrate:
                stem += "_" + self.bitrate + "bit"
            return self.input_file.with_name(stem + self.mode.codec_ext)
        if self.mode.to_mp3:
            return self.input_file.with_suffix(".mp3")
        return self.intermediate_wav

    def existing_output_warnings(self) -> list[str]:
        """Translation keys of overwrite questions to ask before converting."""
        keys = []
        if self.mode.uses_intermediate_wav and self.intermediate_wav.exists():
            keys.append("wav_exists_continue")
        if self.final_file.exists():
            keys.append("file_exists_continue")
        return keys


@dataclass
class ConversionResult:
    succeeded: bool
    ask_delete_wav: bool = False
    ask_play: bool = False
    error_key: str = ""
    error_message: str = ""
    output_file: Optional[Path] = None
    intermediate_wav: Optional[Path] = None


class ConversionError(Exception):
    def __init__(self, key: str, detail: str = ""):
        super().__init__(detail or key)
        self.key = key
        self.detail = detail


# --------------------------------------------------------------------------
# Logging (same format as conversion_commands.log / conversion_errors.log)
# --------------------------------------------------------------------------

@dataclass
class LogContext:
    level: ToolOptionLevel = ToolOptionLevel.BASIC
    console: str = ""
    conversion: str = ""
    input_file: str = ""
    output_file: str = ""
    bitrate: str = ""
    options: str = ""
    custom_encode_args: str = ""
    custom_decode_args: str = ""


def _safe(value: str) -> str:
    return value if value else "(none)"


class ConversionLog:
    def __init__(self, directory: Optional[Path] = None):
        self.directory = directory or state_dir()
        self.error_log = self.directory / "conversion_errors.log"
        self.command_log = self.directory / "conversion_commands.log"

    def _append(self, path: Path, text: str) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

    def command(self, tool: str, args: list[str], output: str, error: str,
                exit_code: int, ctx: Optional[LogContext]) -> None:
        level = ctx.level if ctx else ToolOptionLevel.BASIC
        cmdline = " ".join(shlex.quote(a) for a in args)
        lines = ["", "--- %s ---" % _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "Level: " + level.name.title()]
        lines += self._context_lines(ctx)
        lines += ["Tool: " + tool, "Arguments: " + cmdline,
                  "Command: " + tool + " " + cmdline,
                  "Result: " + ("Success" if exit_code == 0 else "Failed"),
                  "ExitCode: %d" % exit_code]
        if level != ToolOptionLevel.BASIC or exit_code != 0:
            lines += ["Output:", output, "Errors:", error]
        if level == ToolOptionLevel.EXPERT:
            lines += ["Expert details:",
                      "CustomEncodeArgs: " + _safe(ctx.custom_encode_args if ctx else ""),
                      "CustomDecodeArgs: " + _safe(ctx.custom_decode_args if ctx else "")]
        lines.append("--------------------------")
        self._append(self.command_log, "\n".join(lines) + "\n")

        if exit_code != 0:
            err = ["", "--- %s ---" % _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "File: " + tool, "ExitCode: %d" % exit_code,
                   "Command: " + tool + " " + cmdline, "Output:", output,
                   "Errors:", error, "--------------------------"]
            self._append(self.error_log, "\n".join(err) + "\n")

    def exception(self, tool: str, args: list[str], message: str,
                  ctx: Optional[LogContext]) -> None:
        cmdline = " ".join(shlex.quote(a) for a in args)
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append(self.error_log, "\nCRITICAL EXCEPTION: %s - %s\n" % (stamp, message))
        lines = ["", "--- %s ---" % stamp,
                 "Level: " + (ctx.level if ctx else ToolOptionLevel.BASIC).name.title()]
        lines += self._context_lines(ctx)
        lines += ["Tool: " + tool, "Arguments: " + cmdline,
                  "Command: " + tool + " " + cmdline, "Result: Exception",
                  "Exception: " + message, "--------------------------"]
        self._append(self.command_log, "\n".join(lines) + "\n")

    @staticmethod
    def _context_lines(ctx: Optional[LogContext]) -> list[str]:
        if ctx is None:
            return []
        return ["Console: " + _safe(ctx.console),
                "Conversion: " + _safe(ctx.conversion),
                "BitRate: " + _safe(ctx.bitrate),
                "Input: " + _safe(ctx.input_file),
                "Output: " + _safe(ctx.output_file),
                "Options: " + _safe(ctx.options)]


# --------------------------------------------------------------------------
# External programs
# --------------------------------------------------------------------------

def _native_binary(name: str) -> Optional[str]:
    """Find a bundled encoder, a development build, or a system installation."""
    override = os.environ.get("AT9AT3_" + name.upper().replace("-", "_"))
    if override:
        found = shutil.which(override) or (override if os.path.isfile(override) else None)
        if found:
            return found
    pkg = Path(__file__).resolve().parent
    candidates = [
        pkg.parent / "bin" / name,
        pkg.parent / "native" / "build" / name,
        pkg.parent / "native" / "build" / "atracdenc" / "src" / name,
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


class Toolchain:
    """Locates and runs the native ATRAC encoders, FFmpeg, and LAME."""

    def __init__(self, assets: Optional[Path] = None, log: Optional[ConversionLog] = None):
        self.assets = assets or find_assets_dir()
        self.log = log or ConversionLog()
        self.at9_encoder = _native_binary("atrac9-encoder")
        self.atracdenc = _native_binary("atracdenc")
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        self.lame = shutil.which("lame")

    def run_native(self, codec: str, args: list[str], ctx: Optional[LogContext]) -> bool:
        exe = self.at9_encoder if codec == "at9" else self.atracdenc
        if not exe:
            raise ConversionError("native_encoder_missing", codec.upper())
        name = "atrac9-encoder" if codec == "at9" else "atracdenc"
        return self._run(name, [exe] + args, args, ctx)

    def run_ffmpeg(self, args: list[str], ctx: Optional[LogContext]) -> bool:
        if not self.ffmpeg:
            raise ConversionError("ffmpeg_missing")
        full = ["-hide_banner", "-nostdin", "-loglevel", "error", "-y"] + args
        return self._run("ffmpeg", [self.ffmpeg] + full, full, ctx)

    def encode_mp3(self, wav: Path, mp3: Path, ctx: Optional[LogContext]) -> bool:
        if self.lame:
            args = ["--quiet", "-V2", str(wav), str(mp3)]
            return self._run("lame", [self.lame] + args, args, ctx)
        if self.ffmpeg:
            return self.run_ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-q:a", "2",
                                    str(mp3)], ctx)
        raise ConversionError("mp3_encoder_missing")

    def probe_audio(self, path: Path) -> dict:
        """Codec, sample rate, channel count, and duration for the first audio stream."""
        if self.ffprobe:
            try:
                out = subprocess.run(
                    [self.ffprobe, "-v", "error", "-select_streams", "a:0",
                     "-show_entries", "stream=codec_name,sample_rate,channels:format=duration",
                     "-of", "json", str(path)],
                    capture_output=True, text=True, timeout=60, check=True).stdout
                probed = json.loads(out)
                stream = probed["streams"][0]
                return {"codec": stream.get("codec_name", ""),
                        "rate": int(stream.get("sample_rate", 0)),
                        "channels": int(stream.get("channels", 0)),
                        "duration": float(probed.get("format", {}).get("duration", 0) or 0)}
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
                pass
        with wave.open(str(path), "rb") as wf:
            codec = {2: "pcm_s16le", 3: "pcm_s24le"}.get(wf.getsampwidth(), "pcm")
            return {"codec": codec, "rate": wf.getframerate(), "channels": wf.getnchannels(),
                    "duration": wf.getnframes() / wf.getframerate()}

    def _run(self, tool: str, cmd: list[str], logged_args: list[str],
             ctx: Optional[LogContext], cwd: Optional[Path] = None,
             env: Optional[dict] = None) -> bool:
        try:
            proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=3600)
        except (OSError, subprocess.SubprocessError) as exc:
            self.log.exception(tool, logged_args, str(exc), ctx)
            return False
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        self.log.command(tool, logged_args, out, err, proc.returncode, ctx)
        return proc.returncode == 0


# --------------------------------------------------------------------------
# Workflow (At9ConversionWorkflow + At3ConversionWorkflow)
# --------------------------------------------------------------------------

def _replace_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _add_riff_loop(path: Path, settings: ToolSettings, rate: int, samples: int) -> None:
    """Insert a standard one-loop smpl chunk into an ATRAC3 RIFF file."""
    if settings.loop_mode == ToolLoopMode.CUSTOM_LOOP:
        start, end = settings.loop_start, settings.loop_end
        if start < 0 or end <= start or end > samples:
            raise ConversionError("invalid_options", "loop must satisfy 0 <= start < end <= input samples")
        end -= 1  # The UI uses end-exclusive input; smpl stores inclusive positions.
    else:
        if samples <= 0:
            raise ConversionError("invalid_options", "cannot loop empty audio")
        start, end = 0, samples - 1
    period = round(1_000_000_000 / rate)
    payload = struct.pack("<9I6I", 0, 0, period, 60, 0, 0, 0, 1, 24,
                          0, 0, start, end, 0, 0)
    chunk = b"smpl" + struct.pack("<I", len(payload)) + payload
    data = bytearray(path.read_bytes())
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ConversionError("conversion_log_error", "native encoder wrote invalid RIFF")
    pos = 12
    while pos + 8 <= len(data):
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if data[pos:pos + 4] == b"data":
            break
        pos += 8 + size + (size & 1)
    else:
        raise ConversionError("conversion_log_error", "ATRAC3 data chunk missing")
    data[pos:pos] = chunk
    struct.pack_into("<I", data, 4, len(data) - 8)
    path.write_bytes(data)


class Converter:
    def __init__(self, toolchain: Optional[Toolchain] = None):
        self.tools = toolchain or Toolchain()

    def convert(self, job: ConversionJob, status: StatusCallback = lambda key: None) -> ConversionResult:
        cache_dir().mkdir(parents=True, exist_ok=True)
        # Native tools see short ASCII names inside a private work directory.
        work = Path(tempfile.mkdtemp(prefix="job-", dir=str(cache_dir())))
        try:
            if job.mode.is_encode:
                return self._encode(job, work, status)
            if job.mode.is_decode:
                return self._decode(job, work, status)
            return ConversionResult(False)
        except ConversionError as exc:
            return ConversionResult(False, error_key=exc.key, error_message=exc.detail)
        except Exception as exc:  # keep the GUI alive, like the C# catch-alls
            self.tools.log.exception(job.tool_name, [], repr(exc), None)
            return ConversionResult(False, error_message=str(exc))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _ctx(self, job: ConversionJob, conversion: str, inp: Path, out: Path,
             options: list[str]) -> LogContext:
        s = job.settings
        return LogContext(level=s.level, console=job.console, conversion=conversion,
                          input_file=str(inp), output_file=str(out), bitrate=job.bitrate,
                          options=" ".join(options), custom_encode_args=s.custom_encode_args,
                          custom_decode_args=s.custom_decode_args)

    # -- encode: WAV/MP3 -> AT9/AT3 -----------------------------------------

    def _encode(self, job: ConversionJob, work: Path, status: StatusCallback) -> ConversionResult:
        if not job.bitrate:
            raise ConversionError("invalid_options", "no bitrate selected")
        if job.bitrate not in BITRATES[job.console]:
            raise ConversionError("invalid_options", "unsupported native bitrate")
        s = job.settings
        if s.level == ToolOptionLevel.EXPERT:
            unsupported = (
                s.use_sampling_rate or s.use_loop_list or s.superframe_mode != 0 or
                s.dual_mode or s.use_quantized_bands or s.use_intensity_band or
                s.use_gradient_mode or s.wide_band or s.band_extension or
                s.lfe_super_low_cut or bool(s.custom_encode_args.strip())
            )
            if unsupported:
                raise ConversionError("native_options_unsupported")

        rate = target_sample_rate(job.console)
        staged_wav = work / "input.wav"
        codec_label = "AT9" if job.mode.is_at9 else "AT3"
        source_label = "MP3" if job.mode.from_mp3 else "WAV"

        # Both native encoders currently accept mono-downmixed/stereo PCM. Use
        # stereo for consistent console-compatible output and 16-bit input for
        # atracdenc; the ATRAC9 raw conversion below promotes it to float64.
        if job.mode.from_mp3:
            status("wav_progress")
            wav = job.intermediate_wav
            self._to_pcm(job.input_file, wav, rate, True,
                         self._ctx(job, "MP3 -> WAV", job.input_file, wav, []))
            shutil.copyfile(wav, staged_wav)
        else:
            info = self.tools.probe_audio(job.input_file)
            if info["rate"] != rate or info["codec"] != "pcm_s16le" or info["channels"] != 2:
                status("normalizing_psp" if job.console == "PSP" else "resampling_wav")
                self._to_pcm(job.input_file, staged_wav, rate, True,
                             self._ctx(job, "WAV normalize", job.input_file, staged_wav, []))
            else:
                shutil.copyfile(job.input_file, staged_wav)

        status("at9_progress" if job.mode.is_at9 else "at3_progress")
        staged_out = work / ("output" + job.mode.codec_ext)
        options: list[str]
        if job.mode.is_at9:
            raw = work / "input.f64le"
            raw_ctx = self._ctx(job, "WAV -> float64 PCM", staged_wav, raw, [])
            if not self.tools.run_ffmpeg(["-i", str(staged_wav), "-map", "0:a:0",
                                          "-c:a", "pcm_f64le", "-f", "f64le", str(raw)], raw_ctx):
                raise ConversionError("wav_preprocess_error", "FFmpeg failed, see conversion log")
            options = [job.bitrate]
            if s.loop_mode == ToolLoopMode.NO_LOOP:
                options.append("--no-loop")
            elif s.loop_mode == ToolLoopMode.CUSTOM_LOOP:
                options += ["--loop", str(s.loop_start), str(s.loop_end)]
            else:
                options.append("--whole-loop")
            args = [str(raw), str(staged_out)] + options
            ok = self.tools.run_native("at9", args, self._ctx(
                job, "%s -> AT9 (native)" % source_label, job.input_file, job.final_file, options))
        else:
            # Sony's PSP and PS3 at3tool modes both produce ATRAC3plus. Plain
            # ATRAC3 only supports the lower PSP rates and is a different codec.
            options = ["-e", "atrac3plus", "--bitrate", job.bitrate,
                       "--container", "riff", "--nostdout"]
            args = options + ["-i", str(staged_wav), "-o", str(staged_out)]
            ok = self.tools.run_native("at3", args, self._ctx(
                job, "%s -> AT3 (native)" % source_label, job.input_file, job.final_file, options))
            if ok and s.loop_mode != ToolLoopMode.NO_LOOP:
                with wave.open(str(staged_wav), "rb") as wf:
                    _add_riff_loop(staged_out, s, wf.getframerate(), wf.getnframes())
        if not ok or not _nonempty(staged_out):
            raise ConversionError("conversion_log_error")
        _replace_file(staged_out, job.final_file)
        return ConversionResult(True, ask_delete_wav=job.mode.from_mp3, ask_play=True,
                                output_file=job.final_file,
                                intermediate_wav=job.intermediate_wav if job.mode.from_mp3 else None)

    def _to_pcm(self, src: Path, dst: Path, rate: int, stereo: bool, ctx: LogContext) -> None:
        args = ["-i", str(src), "-map", "0:a:0", "-ar", str(rate)]
        if stereo:
            args += ["-ac", "2"]
        args += ["-c:a", "pcm_s16le", "-f", "wav", str(dst)]
        if not self.tools.run_ffmpeg(args, ctx) or not _nonempty(dst):
            raise ConversionError("wav_preprocess_error", "ffmpeg failed, see conversion log")

    # -- decode: AT9/AT3 -> WAV/MP3 -----------------------------------------

    def _decode(self, job: ConversionJob, work: Path, status: StatusCallback) -> ConversionResult:
        codec_label = "AT9" if job.mode.is_at9 else "AT3"
        status("wav_progress")
        wav = job.intermediate_wav
        staged_in = work / ("input" + job.mode.codec_ext)
        staged_out = work / "output.wav"
        shutil.copyfile(job.input_file, staged_in)
        s = job.settings
        if s.level == ToolOptionLevel.EXPERT and (s.custom_decode_args.strip() or
                                                  s.wave_extensible_header):
            raise ConversionError("native_options_unsupported")
        pcm = {ToolPcmOutputFormat.INT16: "pcm_s16le",
               ToolPcmOutputFormat.INT24: "pcm_s24le",
               ToolPcmOutputFormat.FLOAT: "pcm_f32le"}[s.pcm_output_format]
        options = ["native", pcm]
        args: list[str] = []
        if s.decode_repeat > 1:
            args += ["-stream_loop", str(s.decode_repeat - 1)]
            options += ["repeat=" + str(s.decode_repeat)]
        # FFmpeg's WAV demuxer may group several ATRAC3plus frames per packet.
        # Rebuild PCM timestamps after decoding so repeated packet PTS values
        # do not produce non-monotonic output timestamps.
        args += ["-i", str(staged_in)]
        duration = self.tools.probe_audio(staged_in).get("duration", 0)
        if duration > 0:
            # Compressed frames pad the tail; the container's exact sample count
            # lets us trim that padding, including after explicit stream repeats.
            args += ["-t", "%.9f" % (duration * s.decode_repeat)]
        args += ["-map", "0:a:0", "-af", "asetpts=N/SR/TB",
                 "-c:a", pcm, str(staged_out)]
        conversion = "%s -> %s" % (codec_label, "MP3 decode" if job.mode.to_mp3 else "WAV")
        ctx = self._ctx(job, conversion, job.input_file, wav, options)
        if not self.tools.run_ffmpeg(args, ctx) or not _nonempty(staged_out):
            raise ConversionError("conversion_log_error")
        _replace_file(staged_out, wav)

        if not job.mode.to_mp3:
            return ConversionResult(True, output_file=wav)
        mp3_ctx = self._ctx(job, codec_label + " -> MP3 encode", wav, job.final_file, ["-V2"])
        if not self.tools.encode_mp3(wav, job.final_file, mp3_ctx) or not _nonempty(job.final_file):
            raise ConversionError("conversion_log_error")
        return ConversionResult(True, ask_delete_wav=True, output_file=job.final_file,
                                intermediate_wav=wav)


def delete_intermediate(result: ConversionResult) -> None:
    if result.intermediate_wav and result.intermediate_wav.exists():
        result.intermediate_wav.unlink()


# --------------------------------------------------------------------------
# Playback helper: decode AT9/AT3 to a temp WAV a normal player can play
# --------------------------------------------------------------------------

def decode_for_playback(path: Path, tools: Toolchain, workdir: Path) -> Path:
    """Decode AT9/AT3 to a temporary WAV using native FFmpeg codecs."""
    out = workdir / "playback.wav"
    if out.exists():
        out.unlink()
    if tools.ffmpeg:
        proc = subprocess.run([tools.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                               "-y", "-i", str(path), "-c:a", "pcm_s16le", str(out)],
                              capture_output=True, timeout=600)
        if proc.returncode == 0 and _nonempty(out):
            return out
    raise ConversionError("playback_error", str(path))
