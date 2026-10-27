"""Conversion core: no Qt imports, so it can be used by the GUI and the CLI.

Mirrors ConversionModels.cs / ConversionService.cs / At9Controller.cs /
At3Controller.cs from the Windows version. NAudio (resampling, MP3 decode) is
replaced by ffmpeg, lame.exe by native lame, and the Sony tools run via Wine.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shlex
import shutil
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
        if (candidate / "ATRAC").is_dir():
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

TOOL_NAMES = {
    "PS4": "PS4_at9tool.exe",
    "PSVita": "PSVita_at9tool.exe",
    "PSP": "PSP_at3tool.exe",
    "PS3": "PS3_at3tool.exe",
}

BITRATES = {
    "PSP": ["32", "48", "52", "64", "66", "96", "105", "128", "132", "160",
            "192", "256", "320", "352"],
    "PS3": ["32", "48", "57", "64", "72", "96", "114", "128", "144", "160",
            "192", "256", "320", "384", "512", "768"],
    "PSVita": ["36", "48", "60", "72", "84", "96", "120", "144", "168", "192"],
    # The Windows list repeats 384 and is slightly out of order; same values.
    "PS4": ["36", "48", "60", "72", "84", "96", "120", "144", "168", "192",
            "240", "288", "300", "336", "360", "384", "420", "480", "504", "672"],
}


def consoles_for(mode: ConversionMode) -> list[str]:
    if mode.is_at9:
        return list(AT9_CONSOLES)
    if mode.is_at3:
        return list(AT3_CONSOLES)
    return []


def target_sample_rate(console: str) -> int:
    return 44100 if console == "PSP" else 48000


# --------------------------------------------------------------------------
# Argument builder (ConversionToolArgumentBuilder)
# --------------------------------------------------------------------------

def build_encode_options(s: Optional[ToolSettings], is_at9: bool, is_ps4: bool,
                         loop_list_arg: Optional[str] = None) -> list[str]:
    if s is None or s.level == ToolOptionLevel.BASIC:
        return ["-wholeloop"]

    args: list[str] = []
    if s.loop_mode == ToolLoopMode.NO_LOOP:
        pass
    elif s.loop_mode == ToolLoopMode.CUSTOM_LOOP:
        args += ["-loop", str(s.loop_start), str(s.loop_end)]
    elif s.loop_mode == ToolLoopMode.DEFAULT_WHOLE_LOOP:
        args.append("-defaultWL" if is_ps4 else "-wholeloop")
    else:
        args.append("-wholeloop")

    if s.level != ToolOptionLevel.EXPERT:
        return args

    if is_at9 and s.use_sampling_rate:
        args += ["-fs", str(s.sampling_rate)]
    if is_at9 and s.use_loop_list and s.loop_list_path.strip():
        args += ["-looplist", loop_list_arg or s.loop_list_path]
    if is_at9 and s.superframe_mode == 1:
        args.append("-supframeon")
    elif is_at9 and s.superframe_mode == 2:
        args.append("-supframeoff")
    if is_at9 and s.dual_mode:
        args.append("-dual")
    if is_at9 and s.use_quantized_bands:
        args += ["-nbands", str(s.quantized_bands)]
    if is_at9 and s.use_intensity_band:
        args += ["-isband", str(s.intensity_band)]
    if is_at9 and s.use_gradient_mode:
        args += ["-gradmode", str(s.gradient_mode)]
    if is_ps4 and s.wide_band:
        args.append("-wband")
    if is_ps4 and s.band_extension:
        args.append("-bex")
    if is_ps4 and s.lfe_super_low_cut:
        args.append("-slc")
    if s.custom_encode_args.strip():
        args += shlex.split(s.custom_encode_args)
    return args


def build_decode_options(s: Optional[ToolSettings], is_ps4: bool) -> list[str]:
    if s is None or s.level == ToolOptionLevel.BASIC:
        return ["-repeat", "1"]

    args = ["-repeat", str(max(1, s.decode_repeat))]
    if s.level == ToolOptionLevel.EXPERT and is_ps4 and s.wave_extensible_header:
        args.append("-wext")
    if is_ps4:  # Advanced or Expert
        args.append({ToolPcmOutputFormat.INT24: "-int24",
                     ToolPcmOutputFormat.FLOAT: "-float"}.get(s.pcm_output_format, "-int16"))
    if s.level == ToolOptionLevel.EXPERT and s.custom_decode_args.strip():
        args += shlex.split(s.custom_decode_args)
    return args


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
        return TOOL_NAMES.get(self.console, "")

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

def find_wine() -> Optional[str]:
    override = os.environ.get("AT9AT3_WINE")
    if override:
        return shutil.which(override) or (override if os.path.isfile(override) else None)
    return shutil.which("wine") or shutil.which("wine64")


def to_windows_path(path: Path) -> str:
    """Wine maps the Unix root to drive Z:."""
    return "Z:" + str(Path(path).resolve()).replace("/", "\\")


class Toolchain:
    """Locates and runs wine, the Sony tools, ffmpeg and lame."""

    def __init__(self, assets: Optional[Path] = None, log: Optional[ConversionLog] = None):
        self.assets = assets or find_assets_dir()
        self.log = log or ConversionLog()
        self.wine = find_wine()
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        self.lame = shutil.which("lame")
        prefix = os.environ.get("AT9AT3_WINEPREFIX")
        self.wine_prefix = Path(prefix).expanduser() if prefix else data_dir() / "wineprefix"

    # -- wine --------------------------------------------------------------

    def tool_path(self, tool_name: str) -> Path:
        return self.assets / "ATRAC" / tool_name

    def wine_env(self) -> dict:
        env = dict(os.environ)
        env["WINEPREFIX"] = str(self.wine_prefix)
        env["WINEDEBUG"] = "-all"
        # No Mono/Gecko install prompts, no desktop menu entries.
        env["WINEDLLOVERRIDES"] = "mscoree=;mshtml=;winemenubuilder.exe="
        return env

    def wine_prefix_ready(self) -> bool:
        return (self.wine_prefix / "system.reg").is_file()

    def ensure_wine(self, status: Optional[StatusCallback] = None) -> None:
        if not self.wine:
            raise ConversionError("wine_missing")
        if self.wine_prefix_ready():
            return
        if status:
            status("wine_preparing")
        self.wine_prefix.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([self.wine, "wineboot", "--init"], env=self.wine_env(),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=600, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConversionError("wine_missing", str(exc))

    def run_sony_tool(self, tool_name: str, args: list[str], workdir: Path,
                      ctx: Optional[LogContext]) -> bool:
        exe = self.tool_path(tool_name)
        if not exe.is_file():
            raise ConversionError("tool_missing", str(exe))
        cmd = [self.wine, to_windows_path(exe)] + args
        return self._run("ATRAC/" + tool_name, cmd, args, ctx, cwd=workdir,
                         env=self.wine_env())

    # -- native ------------------------------------------------------------

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
        """{'codec': str, 'rate': int, 'channels': int} for the first audio stream."""
        if self.ffprobe:
            try:
                out = subprocess.run(
                    [self.ffprobe, "-v", "error", "-select_streams", "a:0",
                     "-show_entries", "stream=codec_name,sample_rate,channels",
                     "-of", "json", str(path)],
                    capture_output=True, text=True, timeout=60, check=True).stdout
                stream = json.loads(out)["streams"][0]
                return {"codec": stream.get("codec_name", ""),
                        "rate": int(stream.get("sample_rate", 0)),
                        "channels": int(stream.get("channels", 0))}
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
                pass
        with wave.open(str(path), "rb") as wf:  # plain PCM only
            codec = {2: "pcm_s16le", 3: "pcm_s24le"}.get(wf.getsampwidth(), "pcm")
            return {"codec": codec, "rate": wf.getframerate(), "channels": wf.getnchannels()}

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


class Converter:
    def __init__(self, toolchain: Optional[Toolchain] = None):
        self.tools = toolchain or Toolchain()

    def convert(self, job: ConversionJob, status: StatusCallback = lambda key: None) -> ConversionResult:
        cache_dir().mkdir(parents=True, exist_ok=True)
        # The Sony tools are old ANSI programs: they only ever see short ASCII
        # names inside a private work dir, so spaces/unicode in paths are fine.
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
        self.tools.ensure_wine(status)

        rate = target_sample_rate(job.console)
        force_stereo = job.console == "PSP"
        staged_wav = work / "input.wav"
        codec_label = "AT9" if job.mode.is_at9 else "AT3"
        source_label = "MP3" if job.mode.from_mp3 else "WAV"

        if job.mode.from_mp3:
            status("wav_progress")
            wav = job.intermediate_wav
            self._to_pcm(job.input_file, wav, rate, force_stereo,
                         self._ctx(job, "MP3 -> WAV", job.input_file, wav, []))
            shutil.copyfile(wav, staged_wav)
        else:
            info = self.tools.probe_audio(job.input_file)
            allowed = ("pcm_s16le",) if job.mode.is_at3 else ("pcm_s16le", "pcm_s24le")
            if (info["rate"] != rate or info["codec"] not in allowed
                    or (force_stereo and info["channels"] != 2)):
                status("normalizing_psp" if job.console == "PSP" else "resampling_wav")
                self._to_pcm(job.input_file, staged_wav, rate, force_stereo,
                             self._ctx(job, "WAV normalize", job.input_file, staged_wav, []))
            else:
                shutil.copyfile(job.input_file, staged_wav)

        loop_list_arg = None
        s = job.settings
        if s.level == ToolOptionLevel.EXPERT and s.use_loop_list and s.loop_list_path.strip():
            staged_list = work / "looplist.txt"
            shutil.copyfile(Path(s.loop_list_path).expanduser(), staged_list)
            loop_list_arg = to_windows_path(staged_list)

        status("at9_progress" if job.mode.is_at9 else "at3_progress")
        staged_out = work / ("output" + job.mode.codec_ext)
        options = build_encode_options(s, job.mode.is_at9, job.is_ps4, loop_list_arg)
        args = ["-e", "-br", job.bitrate] + options + [to_windows_path(staged_wav),
                                                       to_windows_path(staged_out)]
        ctx = self._ctx(job, "%s -> %s" % (source_label, codec_label),
                        job.input_file, job.final_file, options)
        if not self.tools.run_sony_tool(job.tool_name, args, work, ctx) or not _nonempty(staged_out):
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
        options = build_decode_options(job.settings, job.is_ps4)
        conversion = "%s -> %s" % (codec_label, "MP3 decode" if job.mode.to_mp3 else "WAV")
        ctx = self._ctx(job, conversion, job.input_file, wav, options)

        if self.tools.wine:
            self.tools.ensure_wine(status)
            args = ["-d"] + options + [to_windows_path(staged_in), to_windows_path(staged_out)]
            ok = self.tools.run_sony_tool(job.tool_name, args, work, ctx)
        elif job.settings.level == ToolOptionLevel.BASIC and self.tools.ffmpeg:
            # No Wine: ffmpeg decodes ATRAC3/3+/9 natively (no loop repeat,
            # which matches the app's default of -repeat 1).
            ok = self.tools.run_ffmpeg(["-i", str(staged_in), "-c:a", "pcm_s16le",
                                        str(staged_out)], ctx)
        else:
            raise ConversionError("wine_missing")
        if not ok or not _nonempty(staged_out):
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
    """ffmpeg first (fast, no Wine); falls back to the Sony tool via Wine."""
    out = workdir / "playback.wav"
    if out.exists():
        out.unlink()
    if tools.ffmpeg:
        proc = subprocess.run([tools.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                               "-y", "-i", str(path), "-c:a", "pcm_s16le", str(out)],
                              capture_output=True, timeout=600)
        if proc.returncode == 0 and _nonempty(out):
            return out
    kind = detect_input_kind(path)
    if tools.wine and kind in ("at9", "at3"):
        tools.ensure_wine()
        staged = workdir / ("play." + kind)
        shutil.copyfile(path, staged)
        tool = "PS4_at9tool.exe" if kind == "at9" else "PS3_at3tool.exe"
        candidates = [tool, "PSVita_at9tool.exe"] if kind == "at9" else [tool, "PSP_at3tool.exe"]
        for name in candidates:
            args = ["-d", "-repeat", "1", to_windows_path(staged), to_windows_path(out)]
            if tools.run_sony_tool(name, args, workdir, None) and _nonempty(out):
                return out
    raise ConversionError("playback_error", str(path))
