"""Language files (LanguageService.cs).

Reads the same lang/*.json files as the Windows build (UTF-8 with BOM), plus
any user translations in ~/.local/share/at9-at3-converter/lang. Missing keys
fall back to English, and a translation whose "version" is older than the
English template is reported as outdated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .core import data_dir, find_assets_dir

_PKG = Path(__file__).resolve().parent
VERSION_KEY = "version"


def _load_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _version(lang: dict) -> int:
    try:
        return int(lang.get(VERSION_KEY, "0"))
    except ValueError:
        return 0


def _linux_strings() -> dict:
    try:
        with open(_PKG / "strings_linux.json", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


@dataclass
class LanguageItem:
    code: str
    name: str


class LanguageService:
    def __init__(self, assets: Path | None = None):
        assets = assets or find_assets_dir()
        # Later directories override earlier ones.
        self.directories = [assets / "lang", data_dir() / "lang"]
        self.linux = _linux_strings()
        self.english = _load_json(_PKG / "en_default.json")
        self.english.update(self.linux.get("en", {}))

    def _files(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for directory in self.directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                if path.name.lower().endswith(".backup.json"):
                    continue
                found[path.stem] = path
        return found

    def available(self) -> list[LanguageItem]:
        items = []
        for code, path in self._files().items():
            lang = _load_json(path)
            if VERSION_KEY not in lang:
                continue
            items.append(LanguageItem(code, lang.get("language", code)))
        if not any(i.code.lower() == "en" for i in items):
            items.insert(0, LanguageItem("en", self.english.get("language", "English")))
        return items

    def load(self, code: str) -> tuple[dict, bool]:
        """Returns (strings, is_outdated)."""
        path = self._files().get(code)
        loaded = _load_json(path) if path else {}
        outdated = code.lower() != "en" and _version(loaded) < _version(self.english)
        merged = dict(self.english)
        merged.update(self.linux.get(code, {}))
        merged.update({k: v for k, v in loaded.items() if v})
        return merged, outdated
