#!/usr/bin/env bash
# Install BMK AT9 & AT3 Converter for Linux.
#
#   ./linux/install.sh               install for the current user (~/.local)
#   sudo ./linux/install.sh --system install for everyone (/usr/local)
#   ./linux/install.sh --uninstall   remove a user install (add --system for system)
#
# PREFIX and DESTDIR are honoured (used by the Arch PKGBUILD).
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
repo="$(dirname "$here")"
app=at9-at3-converter
mode=install
system=0
for arg in "$@"; do
    case "$arg" in
        --system) system=1 ;;
        --uninstall) mode=uninstall ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
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
    echo "Your Wine prefix, logs and settings are kept in:"
    echo "  ~/.local/share/$app  ~/.local/state/$app  ~/.config/$app"
    exit 0
fi

src_assets="$repo/at3-at9_Converter"
[[ -d "$src_assets/ATRAC" ]] || { echo "Can't find $src_assets/ATRAC" >&2; exit 1; }

install -d "$appdir/at9at3conv" "$appdir/assets/ATRAC" "$appdir/assets/Resources" \
           "$appdir/assets/lang" "$bindir" "$share/applications" \
           "$share/icons/hicolor/256x256/apps"
install -m644 "$here"/at9at3conv/*.py "$here"/at9at3conv/*.json "$appdir/at9at3conv/"
install -m755 "$here/$app" "$appdir/$app"
install -m644 "$here/icon.png" "$appdir/icon.png"
# Only the Sony tools and their runtime DLLs are needed (no lame.exe/NAudio/MiniPlayer).
install -m644 "$src_assets"/ATRAC/*.exe "$src_assets"/ATRAC/*.dll "$appdir/assets/ATRAC/"
install -m644 "$src_assets"/Resources/*.png "$appdir/assets/Resources/"
install -m644 "$src_assets"/lang/*.json "$appdir/assets/lang/"
ln -sf "$PREFIX/lib/$app/$app" "$bindir/$app"
install -m644 "$here/$app.desktop" "$share/applications/$app.desktop"
install -m644 "$here/icon.png" "$share/icons/hicolor/256x256/apps/$app.png"

if [[ -z "$DESTDIR" ]]; then
    command -v update-desktop-database >/dev/null && update-desktop-database -q "$share/applications" || true
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -qtf "$share/icons/hicolor" 2>/dev/null || true

    echo "Installed $app to $PREFIX."
    missing=()
    command -v wine >/dev/null || command -v wine64 >/dev/null || missing+=(wine)
    command -v ffmpeg >/dev/null || missing+=(ffmpeg)
    python3 -c 'import PySide6' 2>/dev/null || missing+=(pyside6)
    command -v lame >/dev/null || echo "Optional: lame (MP3 output falls back to ffmpeg without it)."
    if (( ${#missing[@]} )); then
        echo "Still needed: ${missing[*]}"
        echo "  Arch/CachyOS: sudo pacman -S --needed wine ffmpeg pyside6 lame"
    fi
    case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Note: $PREFIX/bin is not in your PATH." ;; esac
fi
