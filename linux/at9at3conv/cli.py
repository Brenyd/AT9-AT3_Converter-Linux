"""Command-line interface (Linux-only extra; same engine as the GUI)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, VERSION
from .core import (AT3_CONSOLES, AT9_CONSOLES, BITRATES, ConversionJob, ConversionMode,
                   Converter, ToolLoopMode, ToolOptionLevel, ToolSettings,
                   delete_intermediate, detect_input_kind, validate_settings)
from .i18n import LanguageService

_MODES = {
    ("wav", "at9"): ConversionMode.WAV_TO_AT9, ("wav", "at3"): ConversionMode.WAV_TO_AT3,
    ("mp3", "at9"): ConversionMode.MP3_TO_AT9, ("mp3", "at3"): ConversionMode.MP3_TO_AT3,
    ("at9", "wav"): ConversionMode.AT9_TO_WAV, ("at9", "mp3"): ConversionMode.AT9_TO_MP3,
    ("at3", "wav"): ConversionMode.AT3_TO_WAV, ("at3", "mp3"): ConversionMode.AT3_TO_MP3,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="at9-at3-converter convert",
        description="Convert WAV/MP3 to AT9/AT3 and back without the GUI.",
        epilog="Bitrates: " + "; ".join("%s: %s" % (c, " ".join(b)) for c, b in BITRATES.items()))
    p.add_argument("input", type=Path)
    p.add_argument("--to", choices=["at9", "at3", "wav", "mp3"],
                   help="output format (default: at9 for WAV/MP3 input, wav for AT9/AT3 input)")
    p.add_argument("--console", choices=AT9_CONSOLES + AT3_CONSOLES,
                   help="target console (default: PS4 for AT9, PSP for AT3)")
    p.add_argument("--bitrate", "-b", help="bitrate in kbps (required when encoding)")
    loop = p.add_mutually_exclusive_group()
    loop.add_argument("--no-loop", action="store_true", help="encode without loop metadata")
    loop.add_argument("--loop", nargs=2, type=int, metavar=("START", "END"),
                      help="custom loop points in samples")
    p.add_argument("--repeat", type=int, default=1, help="loop repeat count when decoding (default 1)")
    p.add_argument("--keep-wav", action="store_true", help="keep the intermediate WAV for MP3 conversions")
    p.add_argument("-y", "--overwrite", action="store_true", help="overwrite existing output files")
    return p


def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    strings, _ = LanguageService().load("en")
    t = lambda key: strings.get(key, key)  # noqa: E731

    src = args.input.expanduser().resolve()
    kind = detect_input_kind(src)
    if not kind or not src.is_file():
        print(t("invalid_audio_file"), file=sys.stderr)
        return 2
    target = args.to or ("at9" if kind in ("wav", "mp3") else "wav")
    mode = _MODES.get((kind, target))
    if mode is None:
        print("Cannot convert %s to %s." % (kind.upper(), target.upper()), file=sys.stderr)
        return 2

    console = args.console or ("PS4" if mode.is_at9 else "PSP")
    if console not in (AT9_CONSOLES if mode.is_at9 else AT3_CONSOLES):
        print("%s is not an %s console." % (console, "AT9" if mode.is_at9 else "AT3"), file=sys.stderr)
        return 2

    settings = ToolSettings()
    if args.no_loop or args.loop or args.repeat != 1:
        settings.level = ToolOptionLevel.ADVANCED
        settings.decode_repeat = args.repeat
        if args.no_loop:
            settings.loop_mode = ToolLoopMode.NO_LOOP
        elif args.loop:
            settings.loop_mode = ToolLoopMode.CUSTOM_LOOP
            settings.loop_start, settings.loop_end = args.loop

    bitrate = ""
    if mode.needs_bitrate:
        if not args.bitrate or args.bitrate not in BITRATES[console]:
            print("Pick --bitrate for %s from: %s" % (console, " ".join(BITRATES[console])),
                  file=sys.stderr)
            return 2
        bitrate = args.bitrate

    error = validate_settings(settings, console, mode)
    if error:
        print(t(error), file=sys.stderr)
        return 2

    job = ConversionJob(mode=mode, input_file=src, console=console, bitrate=bitrate,
                        settings=settings)
    if job.existing_output_warnings() and not args.overwrite:
        print(t("file_exists_continue") + " (use -y)", file=sys.stderr)
        return 1

    print("%s  [%s%s]" % (mode.display_text, console, (" %s kbps" % bitrate) if bitrate else ""))
    result = Converter().convert(job, lambda key: print("  " + t(key)))
    if not result.succeeded:
        msg = t(result.error_key) if result.error_key else t("error")
        if result.error_message:
            msg += " " + result.error_message
        print(msg, file=sys.stderr)
        if result.error_key == "conversion_log_error":
            from .core import ConversionLog
            print("Log: %s" % ConversionLog().command_log, file=sys.stderr)
        return 1
    if result.ask_delete_wav and not args.keep_wav:
        delete_intermediate(result)
    print(t("finish") + " -> " + str(result.output_file))
    return 0


def tools_status_cmd() -> int:
    from .core import Toolchain
    tc = Toolchain()
    print("  ATRAC9   %s" % (tc.at9_encoder or "missing"))
    print("  ATRAC3   %s" % (tc.atracdenc or "missing"))
    print("  ffmpeg   %s" % (tc.ffmpeg or "missing"))
    print("  lame     %s" % (tc.lame or "not installed (FFmpeg is used for MP3)"))
    return 0 if tc.at9_encoder and tc.atracdenc and tc.ffmpeg else 1


def main_version() -> str:
    return "%s %s (Linux)" % (APP_NAME, VERSION)
