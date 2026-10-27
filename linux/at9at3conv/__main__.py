"""Entry point.

  at9-at3-converter                 open the GUI
  at9-at3-converter FILE            open the GUI with FILE loaded
  at9-at3-converter convert ...     convert from the command line (see --help)
  at9-at3-converter import-tools PATH...
                                    import your own copies of Sony's at9tool/at3tool
  at9-at3-converter tools           show which tools are installed
"""

import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "convert":
        from .cli import run
        return run(argv[1:])
    if argv and argv[0] == "import-tools":
        from .cli import import_tools_cmd
        return import_tools_cmd(argv[1:])
    if argv and argv[0] == "tools":
        from .cli import tools_status_cmd
        return tools_status_cmd()
    if argv and argv[0] in ("-V", "--version"):
        from .cli import main_version
        print(main_version())
        return 0
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0

    try:
        from .gui import run_gui
    except ImportError as exc:
        if "PySide6" not in str(exc):
            raise
        print("The GUI needs PySide6 (Qt for Python).\n"
              "  Arch/CachyOS:   sudo pacman -S pyside6\n"
              "  Fedora:         sudo dnf install python3-pyside6\n"
              "  Debian/Ubuntu:  sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtgui\n"
              "  or:             pip install --user PySide6\n"
              "The command line works without it: at9-at3-converter convert --help",
              file=sys.stderr)
        return 1
    return run_gui(argv)


if __name__ == "__main__":
    sys.exit(main())
