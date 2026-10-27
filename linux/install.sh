#!/usr/bin/env bash
# Install BMK AT9 & AT3 Converter for Linux with native ATRAC codecs.
#
#   ./linux/install.sh                  install for the current user (~/.local)
#   sudo ./linux/install.sh --system    install for everyone (/usr/local)
#   ./linux/install.sh --uninstall      remove a user install (add --system for system)
#
# PREFIX and DESTDIR are honoured (used by the Arch PKGBUILD).
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
repo="$(dirname "$here")"
app=at9-at3-converter
mode=install
system=0
while (( $# )); do
    case "$1" in
        --system) system=1 ;;
        --uninstall) mode=uninstall ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
if [[ -z "${PREFIX:-}" ]]; then
    if [[ $system == 1 ]]; then PREFIX=/usr/local; else PREFIX="$HOME/.local"; fi
fi
DESTDIR="${DESTDIR:-}"
share="$DESTDIR$PREFIX/share"
appdir="$DESTDIR$PREFIX/lib/$app"
bindir="$DESTDIR$PREFIX/bin"

if [[ $mode == uninstall ]]; then
    rm -rf "$appdir"
    rm -f "$bindir/$app" "$share/applications/$app.desktop" \
          "$share/icons/hicolor/256x256/apps/$app.png"
    echo "Removed $app from $PREFIX."
    echo "Logs and settings are kept in ~/.local/state/$app and ~/.config/$app."
    exit 0
fi

src_assets="$repo/at3-at9_Converter"
[[ -d "$src_assets/Resources" ]] || { echo "Can't find $src_assets/Resources" >&2; exit 1; }
bash "$here/native/build.sh"

rm -rf "$appdir"
install -d "$appdir/at9at3conv" "$appdir/assets/Resources" "$appdir/assets/lang" \
           "$appdir/bin" "$appdir/licenses/at9" "$appdir/licenses/atracdenc" \
           "$bindir" "$share/applications" "$share/icons/hicolor/256x256/apps"
install -m644 "$here"/at9at3conv/*.py "$here"/at9at3conv/*.json "$appdir/at9at3conv/"
install -m755 "$here/$app" "$appdir/$app"
install -m755 "$here/native/build/atrac9-encoder" "$appdir/bin/atrac9-encoder"
install -m755 "$here/native/build/atracdenc/src/atracdenc" "$appdir/bin/atracdenc"
install -m644 "$here/native/at9/LICENSE" "$here/native/at9/NOTICES.md" \
              "$appdir/licenses/at9/"
install -m644 "$here/native/atracdenc/LICENSE" "$appdir/licenses/atracdenc/"
install -m644 "$here/icon.png" "$appdir/icon.png"
install -m644 "$src_assets"/Resources/*.png "$appdir/assets/Resources/"
install -m644 "$src_assets"/lang/*.json "$appdir/assets/lang/"
ln -sf "$PREFIX/lib/$app/$app" "$bindir/$app"
install -m644 "$here/$app.desktop" "$share/applications/$app.desktop"
install -m644 "$here/icon.png" "$share/icons/hicolor/256x256/apps/$app.png"

[[ -n "$DESTDIR" ]] && exit 0

command -v update-desktop-database >/dev/null && update-desktop-database -q "$share/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -qtf "$share/icons/hicolor" 2>/dev/null || true
echo "Installed $app to $PREFIX with native ATRAC9 and ATRAC3/3+ encoders."

missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
python3 -c 'import PySide6' 2>/dev/null || missing+=(pyside6)
command -v lame >/dev/null || echo "Optional: lame (MP3 output falls back to FFmpeg without it)."
if (( ${#missing[@]} )); then
    echo "Still needed: ${missing[*]}"
    echo "  Arch/CachyOS: sudo pacman -S --needed ffmpeg pyside6 lame"
fi
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Note: $PREFIX/bin is not in your PATH." ;; esac
