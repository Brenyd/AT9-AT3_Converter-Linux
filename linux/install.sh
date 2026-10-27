#!/usr/bin/env bash
# Install BMK AT9 & AT3 Converter for Linux.
#
#   ./linux/install.sh                  install for the current user (~/.local)
#   ./linux/install.sh --tools DIR      ...and import your Sony at9tool/at3tool copies from DIR
#   sudo ./linux/install.sh --system    install for everyone (/usr/local)
#   ./linux/install.sh --uninstall      remove a user install (add --system for system)
#
# Sony's tools are not included: each user imports their own copies
# (--tools, `at9-at3-converter import-tools DIR`, or "Sony tools" in the app).
# PREFIX and DESTDIR are honoured (used by the Arch PKGBUILD).
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
repo="$(dirname "$here")"
app=at9-at3-converter
mode=install
system=0
tools=""
while (( $# )); do
    case "$1" in
        --system) system=1 ;;
        --uninstall) mode=uninstall ;;
        --tools) shift; tools="${1:?--tools needs a folder or file}" ;;
        --tools=*) tools="${1#--tools=}" ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
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
user_tools="${XDG_DATA_HOME:-$HOME/.local/share}/$app/tools"

if [[ $mode == uninstall ]]; then
    rm -rf "$appdir"
    rm -f "$bindir/$app" "$share/applications/$app.desktop" \
          "$share/icons/hicolor/256x256/apps/$app.png"
    echo "Removed $app from $PREFIX."
    echo "Your imported Sony tools, Wine prefix, logs and settings are kept in:"
    echo "  ~/.local/share/$app  ~/.local/state/$app  ~/.config/$app"
    exit 0
fi

src_assets="$repo/at3-at9_Converter"
[[ -d "$src_assets/Resources" ]] || { echo "Can't find $src_assets/Resources" >&2; exit 1; }

# Older versions of this script copied the Sony tools into the install folder.
# Keep the user's copies by moving them to the per-user tools folder first.
if [[ -z "$DESTDIR" && $system == 0 && -d "$appdir/assets/ATRAC" ]]; then
    mkdir -p "$user_tools"
    for f in "$appdir"/assets/ATRAC/*; do
        [[ -e "$f" && ! -e "$user_tools/$(basename "$f")" ]] && cp "$f" "$user_tools/"
    done
fi
rm -rf "$appdir"

install -d "$appdir/at9at3conv" "$appdir/assets/Resources" "$appdir/assets/lang" \
           "$bindir" "$share/applications" "$share/icons/hicolor/256x256/apps"
install -m644 "$here"/at9at3conv/*.py "$here"/at9at3conv/*.json "$appdir/at9at3conv/"
install -m755 "$here/$app" "$appdir/$app"
install -m644 "$here/icon.png" "$appdir/icon.png"
install -m644 "$src_assets"/Resources/*.png "$appdir/assets/Resources/"
install -m644 "$src_assets"/lang/*.json "$appdir/assets/lang/"
ln -sf "$PREFIX/lib/$app/$app" "$bindir/$app"
install -m644 "$here/$app.desktop" "$share/applications/$app.desktop"
install -m644 "$here/icon.png" "$share/icons/hicolor/256x256/apps/$app.png"

[[ -n "$DESTDIR" ]] && exit 0

command -v update-desktop-database >/dev/null && update-desktop-database -q "$share/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -qtf "$share/icons/hicolor" 2>/dev/null || true
echo "Installed $app to $PREFIX."

# Import the user's own Sony tools: from --tools, or any copies the user
# dropped into the (git-ignored) at3-at9_Converter/ATRAC folder.
if [[ $system == 1 ]]; then
    [[ -n "$tools" ]] && echo "Run the import as your normal user (not root): $app import-tools \"$tools\""
else
    sources=()
    [[ -n "$tools" ]] && sources+=("$tools")
    compgen -G "$src_assets/ATRAC/*.[eE][xX][eE]" >/dev/null && sources+=("$src_assets/ATRAC")
    if (( ${#sources[@]} )); then
        "$appdir/$app" import-tools "${sources[@]}" || true
    fi
fi

missing=()
command -v wine >/dev/null || command -v wine64 >/dev/null || missing+=(wine)
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
python3 -c 'import PySide6' 2>/dev/null || missing+=(pyside6)
command -v lame >/dev/null || echo "Optional: lame (MP3 output falls back to ffmpeg without it)."
if (( ${#missing[@]} )); then
    echo "Still needed: ${missing[*]}"
    echo "  Arch/CachyOS: sudo pacman -S --needed wine ffmpeg pyside6 lame"
fi
if [[ $system == 0 ]] && ! compgen -G "$user_tools/*.exe" >/dev/null; then
    echo "No Sony tools imported yet. Decoding works without them; for encoding run:"
    echo "  $app import-tools /path/to/your/at9tool-and-at3tool"
fi
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Note: $PREFIX/bin is not in your PATH." ;; esac
