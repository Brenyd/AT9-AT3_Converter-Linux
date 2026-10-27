# AT9 & AT3 Converter — Native Linux

A native Linux port of **BMK AT9 & AT3 Converter 2.7** for converting audio for PS4, PSVita, PSP, and PS3.

The Linux version now performs **ATRAC9 and ATRAC3plus encoding entirely natively**. It does not use Wine, Sony's proprietary `at9tool`/`at3tool`, Windows executables, or a Wine prefix.

## Features

- Convert WAV or MP3 to AT9 for PS4 and PSVita.
- Convert WAV or MP3 to ATRAC3plus AT3 for PSP and PS3.
- Decode AT9 and AT3 to WAV or MP3.
- Select the supported bitrate for each target console.
- Use whole-file, disabled, or custom loop metadata.
- Play AT9 and AT3 files through the system audio player.
- Use the graphical Qt interface or the command-line converter.
- English, French, and Spanish translations.
- Native Linux paths, including paths containing spaces or non-English characters.

## How the native version works

- **ATRAC9 encoding:** bundled native C++ encoder derived from BlackBearReloaded's PS5 AT9 Converter, with LibAtrac9 tables.
- **ATRAC3plus encoding:** bundled native C++ [`atracdenc`](https://github.com/dcherednik/atracdenc) encoder with bitrate-aware RIFF output.
- **Decoding and audio preparation:** FFmpeg.
- **MP3 encoding:** native LAME when installed, otherwise FFmpeg.
- **Playback:** PipeWire, PulseAudio, ALSA, or ffplay.
- **Graphical interface:** PySide6/Qt.

No Sony tools need to be downloaded or imported.

## Supported output

| Target | Codec | Sample rate | Channels | Bitrates |
|---|---|---:|---:|---|
| PS4 | ATRAC9 (`.at9`) | 48 kHz | Stereo | 72, 96, 120, 144, 168, 192 kbps |
| PSVita | ATRAC9 (`.at9`) | 48 kHz | Stereo | 72, 96, 120, 144, 168, 192 kbps |
| PSP | ATRAC3plus (`.at3`) | 44.1 kHz | Stereo | 66, 105, 132, 146, 176, 265, 352 kbps |
| PS3 | ATRAC3plus (`.at3`) | 44.1 kHz | Stereo | 64, 96, 128, 192, 256, 320, 352 kbps |

Basic options and Advanced loop/PCM options are supported. Legacy Expert switches specific to Sony's tools, multichannel encoding, and Sony loop-list files are not currently supported.

The generated files have been encode/decode tested with FFmpeg. Compatibility with physical consoles still requires hardware testing.

## Install on Arch Linux or CachyOS

Install the build and runtime dependencies:

```bash
sudo pacman -S --needed git base-devel cmake libsndfile ffmpeg pyside6 lame
```

Clone the native branch and run the installer:

```bash
git clone --branch native-codecs --single-branch https://github.com/Brenyd/AT9-AT3_Converter-Linux.git
cd AT9-AT3_Converter-Linux
./linux/install.sh
```

The installer compiles both native encoders and installs the application under `~/.local`. It also adds an application-menu entry.

After installation, verify that the native tools were found:

```bash
~/.local/bin/at9-at3-converter tools
```

Launch the application from your menu as **AT9 & AT3 Converter**, or run:

```bash
~/.local/bin/at9-at3-converter
```

If `~/.local/bin` is in your `PATH`, you can use `at9-at3-converter` without the full path.

## Other Linux distributions

Install these dependencies with your package manager:

- Python 3
- PySide6
- A C++17 compiler
- CMake and Make
- libsndfile development files
- FFmpeg
- Git
- LAME (optional; FFmpeg can encode MP3 instead)

Then clone the `native-codecs` branch and run `./linux/install.sh` as shown above.

To build only the native codecs without installing the application:

```bash
./linux/native/build.sh
```

After building, the application can also be run directly from the checkout:

```bash
./linux/at9-at3-converter
```

## Command-line use

Convert WAV to AT9:

```bash
at9-at3-converter convert theme.wav --to at9 --console PSVita -b 144
# Output: theme_144bit.at9
```

Convert MP3 to ATRAC3plus AT3:

```bash
at9-at3-converter convert song.mp3 --to at3 --console PSP -b 132
# Output: song_132bit.at3
```

Convert AT9 to MP3:

```bash
at9-at3-converter convert bgm.at9 --to mp3
# Output: bgm.mp3
```

Encode without loop metadata:

```bash
at9-at3-converter convert theme.wav --to at9 --console PS4 -b 144 --no-loop
```

Encode with custom loop points, expressed in input samples:

```bash
at9-at3-converter convert theme.wav --to at9 --console PS4 -b 144 --loop 0 48000
```

Show all command-line options:

```bash
at9-at3-converter convert --help
```

Show native encoder and audio-tool paths:

```bash
at9-at3-converter tools
```

## Logs and troubleshooting

Conversion logs are stored in:

```text
~/.local/state/at9-at3-converter/
```

- `conversion_commands.log` contains the commands and results.
- `conversion_errors.log` contains conversion failures.

When a conversion fails in the GUI, use the **Open log** link in the status bar. The **Native codecs** status link shows which encoder binaries the application is using.

## Uninstall

From the repository, run:

```bash
./linux/install.sh --uninstall
```

This removes the installed application. Logs and settings are kept.

## Source notices

The native codec sources and their license notices are included under [`linux/native/`](linux/native/). Sony's proprietary encoder executables are neither included nor required.

[Changelog](Release.md)

## Screenshots

<img width="412" height="426" alt="Main window" src="https://github.com/user-attachments/assets/aec42908-9056-46c0-9218-b3cf6c884906" />

<img width="412" height="426" alt="Conversion window" src="https://github.com/user-attachments/assets/fe8bc7bc-7470-4466-a0e6-2ab4cdaa2eaa" />

<img width="622" height="602" alt="Tool options" src="https://github.com/user-attachments/assets/b5bc04be-a4c0-4441-bfde-182b15b8cd80" />
