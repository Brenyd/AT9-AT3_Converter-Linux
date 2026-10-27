# Sony ATRAC tools (not included)

Sony's `at9tool` / `at3tool` programs are not distributed with this project.
Use your own copies:

- **Linux app:** run `at9-at3-converter import-tools /path/to/your/tools`,
  click **Sony tools** in the app's status bar, or pass `--tools DIR` to
  `linux/install.sh`. Files are recognised by content, so their names don't matter.
- **Windows build:** put your copies here as `PS4_at9tool.exe`,
  `PSVita_at9tool.exe`, `PSP_at3tool.exe` and `PS3_at3tool.exe`.

Files you put in this folder are ignored by git.
