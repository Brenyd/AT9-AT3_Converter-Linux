# AT9-AT3_Converter

## 🐧 Linux port

A native Linux version of BMK AT9 & AT3 Converter 2.7 lives in [`linux/`](linux). Same features as the Windows app: WAV/MP3 → AT9 (PS4, PSVita) / AT3 (PSP, PS3), AT9/AT3 → WAV/MP3, per-console bitrates, Basic/Advanced/Expert tool options, loop settings, AT9/AT3 playback, and the English/French/Spanish translations.

How it works:

- The UI is Qt (PySide6), so it fits right in on KDE Plasma and other desktops.
- **Sony's `at9tool`/`at3tool` are not included.** You import your own copies once (see below). They're recognised by their contents, so the file names don't matter. They're closed-source Windows programs, so they run through **Wine** in their own prefix (`~/.local/share/at9-at3-converter/wineprefix`), which is set up automatically on first use.
- **Decoding (AT9/AT3 → WAV/MP3) and playback are fully native** with ffmpeg, and need neither the Sony tools nor Wine. Those are only used for encoding and for the Advanced/Expert decode options.
- What NAudio, `lame.exe` and `MiniPlayer.exe` did on Windows is done natively: **ffmpeg** for resampling and MP3 decoding, **lame** (or ffmpeg) for MP3 encoding, and PipeWire / PulseAudio / ALSA for playback.
- Files can be anywhere, with spaces or non-English characters in their names. They no longer have to sit next to the app.

### Install (Arch / CachyOS)

```sh
sudo pacman -S --needed wine ffmpeg pyside6 lame
git clone https://github.com/Brenyd/AT9-AT3_Converter-Linux.git
cd AT9-AT3_Converter-Linux
./linux/install.sh --tools ~/path/to/your/sony-tools   # installs to ~/.local, adds a menu entry
```

`--tools` accepts a folder or individual files. You can also import later with `at9-at3-converter import-tools DIR`, or by clicking **Sony tools** in the app's status bar, which also shows which of the four tools (PS4, PSVita, PSP, PS3) you have. Imported copies live in `~/.local/share/at9-at3-converter/tools/`.

Or build a package: `cd linux && makepkg -si`, then import your tools as above. Wine is an optional dependency, since it's only needed for encoding.

On other distros, install ffmpeg, PySide6, Wine (for encoding) and (optionally) lame with your package manager, then run `./linux/install.sh`. You can also run the app straight from the checkout with `./linux/at9-at3-converter`. To remove it, run `./linux/install.sh --uninstall`.

### Use

Launch **AT9 & AT3 Converter** from your app menu. Drop a file on the window, or click the drop bar to browse. The first conversion takes a little longer while Wine sets itself up.

There's also a command line:

```sh
at9-at3-converter convert theme.wav --console PSVita -b 144          # -> theme_144bit.at9
at9-at3-converter convert song.mp3 --to at3 --console PSP -b 132     # -> song_132bit.at3
at9-at3-converter convert bgm.at9 --to mp3                           # -> bgm.mp3
at9-at3-converter convert --help
```

`at9-at3-converter tools` lists which Sony tools, Wine, ffmpeg and lame were found.

Logs (`conversion_commands.log`, `conversion_errors.log`) are written to `~/.local/state/at9-at3-converter/`. When a conversion fails, the status bar shows an **Open log** link.

---

**FR:** Petit programme pour convertir vos fichiers Wav/MP3 en at9/at3 et at9/at3 en wav/MP3 rien d'extraordinaire. Il faudra .NET framework 4.5 d'installer et les fichiers à convertir
doivent être dans le même répertoire que l'application.
Le format At9&At3 sont utilisé par Sony sur la PSVita/TV, PSP, PS3 et la PS4 notamment pour les sons des thèmes.
At9&At3_Converter vous permet de convertir ces fichiers sonores vers d'autres formats


**EN:** Little program for convert your files Wav/MP3 To At9/At3 and At9/At3 To Wav/Mp3 nothing special you must have .NET framework 4.5 installed and the files to be converted must be in the same directory as the application.
The At9&At3 format are used by Sony on PSVita/TV, PSP, PS3 and PS4 especially for the sounds of themes.
At9&At3_Converter allows you to convert these sound files to other formats 

[Changelog](Release.md)

# Screenshots

<img width="412" height="426" alt="Main" src="https://github.com/user-attachments/assets/aec42908-9056-46c0-9218-b3cf6c884906" />

<img width="412" height="426" alt="Convert" src="https://github.com/user-attachments/assets/fe8bc7bc-7470-4466-a0e6-2ab4cdaa2eaa" />

<img width="622" height="602" alt="expert" src="https://github.com/user-attachments/assets/b5bc04be-a4c0-4441-bfde-182b15b8cd80" />
