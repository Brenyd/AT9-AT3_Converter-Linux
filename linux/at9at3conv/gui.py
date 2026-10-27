"""Qt (PySide6) GUI: port of MainForm, DialogService, the bitrate info forms
and ToolOptionsDialog from the Windows build."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel, QLayout,
                               QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QStatusBar, QVBoxLayout, QWidget)

from . import APP_ID, APP_NAME, VERSION
from .core import (BITRATES, TOOL_NAMES, ConversionJob, ConversionLog, ConversionMode, ConversionResult,
                   Converter, Toolchain, ToolLoopMode, ToolOptionLevel, ToolPcmOutputFormat,
                   ToolSettings, consoles_for, delete_intermediate, detect_input_kind,
                   find_assets_dir, import_tools, validate_settings)
from .i18n import LanguageService
from .player import AudioPlayer

STUDIO_URL = "https://github.com/BenMitnicK"
ACTIVE_BG, DISABLED_BG, DISABLED_FG = "#2563eb", "#e5e7eb", "#374151"
PANEL_BG, LIGHT_BG = "#0072c5", "#ecf4ff"

Translate = Callable[[str], str]


def _wrap(text: str, width: int = 48) -> str:
    return textwrap.fill(text, width) if text else text


def style_button(button: QPushButton, enabled: bool) -> None:
    """Blue when usable, grey when not (SetConvertButtonEnabled & co)."""
    button.setEnabled(enabled)
    bg, fg = (ACTIVE_BG, "white") if enabled else (DISABLED_BG, DISABLED_FG)
    button.setStyleSheet(
        "QPushButton { background:%s; color:%s; border:1px solid #1e3a8a; border-radius:4px;"
        " padding:2px 6px; font-weight:bold; }" % (bg, fg))


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)


# --------------------------------------------------------------------------
# Dialogs (DialogService.cs)
# --------------------------------------------------------------------------

class Dialogs:
    def __init__(self, parent: QWidget, t: Translate):
        self.parent, self.t = parent, t

    def _box(self, icon, title: str, text: str) -> QMessageBox:
        box = QMessageBox(self.parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        return box

    def two_choice(self, text: str, left: str, right: str) -> bool:
        """True for the left button. Closing the dialog picks the right one,
        as in the Windows version."""
        box = self._box(QMessageBox.Icon.Question, self.t("question"), text)
        left_btn = box.addButton(left, QMessageBox.ButtonRole.AcceptRole)
        right_btn = box.addButton(right, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(left_btn)
        box.setEscapeButton(right_btn)
        box.exec()
        clicked = box.clickedButton()
        return clicked is not None and box.buttonRole(clicked) == QMessageBox.ButtonRole.AcceptRole

    def confirm(self, text: str) -> bool:
        return self.two_choice(text, self.t("yes"), self.t("no"))

    def ok(self, title: str, text: str, icon=QMessageBox.Icon.Information) -> None:
        box = self._box(icon, title, text)
        box.addButton(self.t("ok"), QMessageBox.ButtonRole.AcceptRole)
        box.exec()


# --------------------------------------------------------------------------
# Bitrate info windows (Ps4BitrateInfo, PsvitaBitrateInfo, PspBitrateInfo, Ps3BitrateInfo)
# --------------------------------------------------------------------------

class BitrateInfoDialog(QDialog):
    IMAGES = {"PS4": "ps4_bitrate.png", "PSVita": "psvita_bitrate.png",
              "PSP": "psp_bitrate.png", "PS3": "ps3_bitrate.png"}

    def __init__(self, console: str, resources: Path, title: str, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("%s - %s" % (title, console))
        pixmap = QPixmap(str(resources / self.IMAGES[console]))
        label = QLabel()
        label.setPixmap(pixmap)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        screen = parent.screen().availableGeometry() if parent.screen() else None
        width, height = pixmap.width() + 24, pixmap.height() + 24
        if screen is not None:
            width, height = min(width, screen.width() - 40), min(height, screen.height() - 80)
        self.resize(max(width, 200), max(height, 150))


# --------------------------------------------------------------------------
# Tool options (ToolOptionsDialog.cs)
# --------------------------------------------------------------------------

class ToolOptionsDialog(QDialog):
    LABEL_WIDTH = 190

    def __init__(self, console: str, mode: ConversionMode, source: ToolSettings,
                 t: Translate, resources: Path, parent: QWidget):
        super().__init__(parent)
        self.console, self.mode, self.t = console, mode, t
        self.resources = resources
        self.settings = source.clone() if source else ToolSettings()
        self.dialogs = Dialogs(self, t)
        self.loading = False
        self.expert_warning_shown = False
        self.previous_level = self.settings.level
        self._mode_pixmaps: dict[str, QPixmap] = {}

        self.setWindowTitle("%s - %s" % (t("tool_options_title"), console))
        self.resize(620, 570)
        self._build()
        self._load()
        self._refresh()

    # -- helpers -----------------------------------------------------------

    @property
    def is_at9(self) -> bool:
        return self.console in ("PS4", "PSVita")

    @property
    def is_ps4(self) -> bool:
        return self.console == "PS4"

    def T(self, key: str, fallback: str) -> str:  # noqa: N802
        value = self.t(key)
        return fallback if value == key else value

    def _section(self, key: str, fallback: str) -> QLabel:
        label = QLabel(self.T(key, fallback))
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        label.setContentsMargins(0, 8, 0, 2)
        self.rows_layout.addWidget(label)
        return label

    def _row(self, first: Optional[QWidget], *controls: QWidget, tooltip: str = "") -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 0, 0, 0)
        holder = first if first is not None else QWidget()
        holder.setFixedWidth(self.LABEL_WIDTH)
        layout.addWidget(holder)
        for control in controls:
            layout.addWidget(control)
        layout.addStretch(1)
        if tooltip:
            for widget in (first,) + controls:
                if widget is not None:
                    widget.setToolTip(_wrap(tooltip))
        self.rows_layout.addWidget(row)
        return row

    def _label(self, key: str, fallback: str) -> QLabel:
        return QLabel(self.T(key, fallback))

    @staticmethod
    def _spin(minimum: int, maximum: int, width: int = 110) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setFixedWidth(width)
        return spin

    @staticmethod
    def _combo(items: list[str], width: int = 160) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setMinimumWidth(width)
        return combo

    # -- layout (BuildLayout / BuildContent) --------------------------------

    def _build(self) -> None:
        t = self.t
        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        level_label = self._label("tool_options_mode", "Mode")
        self.level_combo = self._combo(["Basic", "Advanced", "Expert"], 130)
        self.level_combo.currentIndexChanged.connect(self._on_level_changed)
        for widget in (level_label, self.level_combo):
            widget.setToolTip(_wrap(t("tooltip_options_mode")))
        top.addWidget(level_label)
        top.addWidget(self.level_combo)
        top.addStretch(1)
        outer.addLayout(top)

        self.content = QWidget()
        self.content.setObjectName("optionsContent")
        self.content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_layout = QHBoxLayout(self.content)
        rows_host = QWidget()
        self.rows_layout = QVBoxLayout(rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        content_layout.addWidget(rows_host, 1)
        self.mode_picture = QLabel()
        self.mode_picture.setFixedSize(140, 140)
        self.mode_picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self.mode_picture, 0, Qt.AlignmentFlag.AlignTop)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.content)
        outer.addWidget(scroll, 1)

        # Encode options
        self.sec_encode = self._section("tool_options_encode_options", "Encode options")
        loop_items = ["Whole loop", "No loop", "Custom loop"] + (["Default whole loop"] if self.is_ps4 else [])
        self.loop_combo = self._combo(loop_items)
        self.loop_combo.currentIndexChanged.connect(lambda _i: self._refresh())
        self.row_loop = self._row(self._label("tool_options_loop", "Loop"), self.loop_combo,
                                  tooltip=t("tooltip_loop_mode"))
        self.loop_start = self._spin(0, 999999999)
        self.row_loop_start = self._row(self._label("tool_options_loop_start", "Loop start"),
                                        self.loop_start, tooltip=t("tooltip_loop_start"))
        self.loop_end = self._spin(1, 999999999)
        self.row_loop_end = self._row(self._label("tool_options_loop_end", "Loop end"),
                                      self.loop_end, tooltip=t("tooltip_loop_end"))

        # Decode options
        self.sec_decode = self._section("tool_options_decode_options", "Decode options")
        self.repeat = self._spin(1, 99)
        self.row_repeat = self._row(self._label("tool_options_repeat", "Repeat"), self.repeat,
                                    tooltip=t("tooltip_decode_repeat"))
        self.pcm_combo = self._combo(["16-bit PCM", "24-bit PCM", "Float PCM"])
        self.row_pcm = self._row(self._label("tool_options_wav_output", "WAV output"),
                                 self.pcm_combo, tooltip=t("tooltip_wav_output"))
        self.wext = QCheckBox(self.T("tool_options_wave_extensible", "WAVE_FORMAT_EXTENSIBLE header"))
        self.row_wext = self._row(None, self.wext, tooltip=t("tooltip_wave_extensible"))

        # Expert encode switches
        self.sec_expert = self._section("tool_options_expert_encode_switches", "Expert encode switches")
        self.fs_check = QCheckBox(self.T("tool_options_sampling_rate", "Sampling rate (-fs)"))
        self.fs_combo = self._combo(["12000", "24000", "48000"], 90)
        self.row_fs = self._row(self.fs_check, self.fs_combo, tooltip=t("tooltip_sampling_rate"))
        self.looplist_check = QCheckBox(self.T("tool_options_loop_list", "Loop list file"))
        self.looplist_edit = QLineEdit()
        self.looplist_edit.setMinimumWidth(200)
        browse = QPushButton(self.T("browse", "Browse..."))
        browse.clicked.connect(self._browse_loop_list)
        self.row_looplist = self._row(self.looplist_check, self.looplist_edit, browse,
                                      tooltip=t("tooltip_loop_list"))
        self.superframe = self._combo(["Default", "On", "Off"], 90)
        self.row_superframe = self._row(self._label("tool_options_superframe", "Superframe"),
                                        self.superframe, tooltip=t("tooltip_superframe"))
        self.dual = QCheckBox(self.T("tool_options_dual_mode", "Dual mode"))
        self.row_dual = self._row(None, self.dual, tooltip=t("tooltip_dual_mode"))
        self.nbands_check = QCheckBox(self.T("tool_options_quantized_bands", "Quantized bands"))
        self.nbands = self._spin(3, 18)
        self.row_nbands = self._row(self.nbands_check, self.nbands, tooltip=t("tooltip_quantized_bands"))
        self.isband_check = QCheckBox(self.T("tool_options_intensity_band", "Intensity band"))
        self.isband = self._spin(-1, 18)
        self.row_isband = self._row(self.isband_check, self.isband, tooltip=t("tooltip_intensity_band"))
        self.grad_check = QCheckBox(self.T("tool_options_gradient_mode", "Gradient mode"))
        self.grad = self._spin(0, 4)
        self.row_grad = self._row(self.grad_check, self.grad, tooltip=t("tooltip_gradient_mode"))
        self.band_combo = self._combo([self.T("tool_options_band_mode_default", "Default"),
                                       self.T("tool_options_wide_band", "Wide band (-wband)"),
                                       self.T("tool_options_band_extension", "Band extension (-bex)")], 200)
        self.row_band = self._row(self._label("tool_options_band_mode", "Band mode"),
                                  self.band_combo, tooltip=t("tooltip_band_mode"))
        self.slc = QCheckBox(self.T("tool_options_lfe_super_low_cut", "LFE super low cut (-slc)"))
        self.row_slc = self._row(None, self.slc, tooltip=t("tooltip_lfe_super_low_cut"))

        # Expert custom arguments
        self.sec_custom = self._section("tool_options_expert_custom_arguments", "Expert custom arguments")
        self.custom_encode = QLineEdit()
        self.custom_encode.setMinimumWidth(260)
        self.row_custom_encode = self._row(self._label("tool_options_encode", "Encode"),
                                           self.custom_encode, tooltip=t("tooltip_custom_encode_args"))
        self.custom_decode = QLineEdit()
        self.custom_decode.setMinimumWidth(260)
        self.row_custom_decode = self._row(self._label("tool_options_decode", "Decode"),
                                           self.custom_decode, tooltip=t("tooltip_custom_decode_args"))
        self.rows_layout.addStretch(1)

        buttons = QDialogButtonBox()
        ok = buttons.addButton(t("ok"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(t("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        ok.setDefault(True)
        ok.clicked.connect(self._on_ok)
        cancel.clicked.connect(self.reject)
        outer.addWidget(buttons)

    def _browse_loop_list(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.T("tool_options_loop_list", "Loop list file"),
                                              self.looplist_edit.text() or str(Path.home()),
                                              "Text (*.txt);;* (*)")
        if path:
            self.looplist_edit.setText(path)
            self.looplist_check.setChecked(True)

    # -- settings <-> controls ---------------------------------------------

    def _load(self) -> None:
        s = self.settings
        self.loading = True
        self.level_combo.setCurrentIndex(int(s.level))
        loop_index = int(s.loop_mode)
        if s.loop_mode == ToolLoopMode.DEFAULT_WHOLE_LOOP and not self.is_ps4:
            loop_index = 0
        self.loop_combo.setCurrentIndex(loop_index)
        self.loop_start.setValue(s.loop_start)
        self.loop_end.setValue(max(1, s.loop_end))
        self.repeat.setValue(max(1, min(99, s.decode_repeat)))
        self.fs_check.setChecked(s.use_sampling_rate)
        index = self.fs_combo.findText(str(s.sampling_rate))
        self.fs_combo.setCurrentIndex(index if index >= 0 else 2)
        self.looplist_check.setChecked(s.use_loop_list)
        self.looplist_edit.setText(s.loop_list_path)
        self.superframe.setCurrentIndex(s.superframe_mode)
        self.dual.setChecked(s.dual_mode)
        self.nbands_check.setChecked(s.use_quantized_bands)
        self.nbands.setValue(s.quantized_bands)
        self.isband_check.setChecked(s.use_intensity_band)
        self.isband.setValue(s.intensity_band)
        self.grad_check.setChecked(s.use_gradient_mode)
        self.grad.setValue(s.gradient_mode)
        self.band_combo.setCurrentIndex(1 if s.wide_band else 2 if s.band_extension else 0)
        self.slc.setChecked(s.lfe_super_low_cut)
        self.wext.setChecked(s.wave_extensible_header)
        self.pcm_combo.setCurrentIndex(int(s.pcm_output_format))
        self.custom_encode.setText(s.custom_encode_args)
        self.custom_decode.setText(s.custom_decode_args)
        self.previous_level = s.level
        self.loading = False

    def _save(self) -> None:
        s = self.settings
        s.level = ToolOptionLevel(max(0, self.level_combo.currentIndex()))
        s.loop_mode = ToolLoopMode(max(0, self.loop_combo.currentIndex()))
        s.loop_start = self.loop_start.value()
        s.loop_end = self.loop_end.value()
        s.decode_repeat = self.repeat.value()
        s.use_sampling_rate = self.fs_check.isChecked()
        s.sampling_rate = int(self.fs_combo.currentText())
        s.use_loop_list = self.looplist_check.isChecked()
        s.loop_list_path = self.looplist_edit.text().strip()
        s.superframe_mode = self.superframe.currentIndex()
        s.dual_mode = self.dual.isChecked()
        s.use_quantized_bands = self.nbands_check.isChecked()
        s.quantized_bands = self.nbands.value()
        s.use_intensity_band = self.isband_check.isChecked()
        s.intensity_band = self.isband.value()
        s.use_gradient_mode = self.grad_check.isChecked()
        s.gradient_mode = self.grad.value()
        s.wide_band = self.band_combo.currentIndex() == 1
        s.band_extension = self.band_combo.currentIndex() == 2
        s.lfe_super_low_cut = self.slc.isChecked()
        s.wave_extensible_header = self.wext.isChecked()
        s.pcm_output_format = ToolPcmOutputFormat(max(0, self.pcm_combo.currentIndex()))
        s.custom_encode_args = self.custom_encode.text().strip()
        s.custom_decode_args = self.custom_decode.text().strip()

    # -- behaviour ---------------------------------------------------------

    def _on_level_changed(self, _index: int) -> None:
        new_level = ToolOptionLevel(max(0, self.level_combo.currentIndex()))
        if not self.loading:
            leaving_basic_encode = (self.mode.is_encode and self.previous_level == ToolOptionLevel.BASIC
                                    and new_level != ToolOptionLevel.BASIC)
            if leaving_basic_encode and self.loop_combo.currentIndex() == 0:
                self.loop_combo.setCurrentIndex(1)
            if new_level == ToolOptionLevel.EXPERT and not self.expert_warning_shown:
                self.expert_warning_shown = True
                self.dialogs.ok(self.t("expert_mode_warning_title"),
                                self.t("expert_mode_warning"), QMessageBox.Icon.Warning)
            self.previous_level = new_level
        self._refresh()

    def _mode_pixmap(self, name: str) -> QPixmap:
        if name not in self._mode_pixmaps:
            pixmap = QPixmap(str(self.resources / name))
            self._mode_pixmaps[name] = pixmap.scaled(
                140, 140, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation) if not pixmap.isNull() else pixmap
        return self._mode_pixmaps[name]

    def _refresh(self) -> None:
        if not hasattr(self, "row_custom_decode"):
            return
        level = ToolOptionLevel(max(0, self.level_combo.currentIndex()))
        advanced = level >= ToolOptionLevel.ADVANCED
        expert = level == ToolOptionLevel.EXPERT
        encode, decode = self.mode.is_encode, self.mode.is_decode
        custom_loop = self.loop_combo.currentIndex() == 2
        at9_expert = expert and self.is_at9
        ps4_expert = expert and self.is_ps4
        ps4_decode = self.is_ps4 and decode

        if expert:
            bg, picture = "#fff4e6", "Expert.png"
        elif advanced:
            bg, picture = "#eff6ff", "Advanced.png"
        else:
            bg, picture = "#f9fafb", None
        self.content.setStyleSheet("#optionsContent { background:%s; }" % bg)
        self.mode_picture.setPixmap(self._mode_pixmap(picture) if picture else QPixmap())
        self.mode_picture.setVisible(picture is not None)

        visible = {
            self.sec_encode: encode and advanced,
            self.row_loop: encode and advanced,
            self.row_loop_start: encode and advanced and custom_loop,
            self.row_loop_end: encode and advanced and custom_loop,
            self.sec_decode: decode and advanced,
            self.row_repeat: decode and advanced,
            self.row_pcm: ps4_decode and advanced,
            self.row_wext: ps4_decode and expert,
            self.sec_expert: encode and (at9_expert or ps4_expert),
            self.row_fs: encode and at9_expert,
            self.row_looplist: encode and at9_expert,
            self.row_superframe: encode and at9_expert,
            self.row_dual: encode and at9_expert,
            self.row_nbands: encode and at9_expert,
            self.row_isband: encode and at9_expert,
            self.row_grad: encode and at9_expert,
            self.row_band: encode and ps4_expert,
            self.row_slc: encode and ps4_expert,
            self.sec_custom: expert and (encode or decode),
            self.row_custom_encode: encode and expert,
            self.row_custom_decode: decode and expert,
        }
        for widget, show in visible.items():
            widget.setVisible(show)

    def _on_ok(self) -> None:
        self._save()
        error = validate_settings(self.settings, self.console, self.mode)
        if error:
            self.dialogs.ok(self.t("invalid_options"), self.t(error), QMessageBox.Icon.Warning)
            return
        self.accept()


# --------------------------------------------------------------------------
# Worker thread
# --------------------------------------------------------------------------

class ConversionThread(QThread):
    status = Signal(str)
    done = Signal(object)

    def __init__(self, converter: Converter, job: ConversionJob):
        super().__init__()
        self.converter, self.job = converter, job

    def run(self) -> None:
        self.done.emit(self.converter.convert(self.job, self.status.emit))


# --------------------------------------------------------------------------
# Main window (MainForm.cs)
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, initial_file: Optional[str] = None):
        super().__init__()
        self.assets = find_assets_dir()
        self.resources = self.assets / "Resources"
        self.languages = LanguageService(self.assets)
        self.strings: dict = dict(self.languages.english)
        self.warned_outdated: set[str] = set()
        self.settings_store = QSettings(APP_ID, APP_ID)
        self.toolchain = Toolchain(self.assets)
        self.converter = Converter(self.toolchain)
        self.player = AudioPlayer(self.toolchain, self)
        self.dialogs = Dialogs(self, self.T)

        self.mode = ConversionMode.NONE
        self.input_file: Optional[Path] = None
        self.console = ""
        self.bitrate = ""
        self.tool_settings = ToolSettings()
        self.status_key = "ready"
        self.worker: Optional[ConversionThread] = None
        self._frozen_state: dict = {}

        self.setWindowTitle("%s  V%s" % (APP_NAME, VERSION))
        icon = self._app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.setAcceptDrops(True)
        self._build()
        self._wire_player()
        self._load_language_list()
        self._reset_ui()

        if initial_file:
            path = Path(initial_file).expanduser()
            if path.is_file():
                QTimer.singleShot(0, lambda: self.load_file(path.resolve()))

    # -- helpers -----------------------------------------------------------

    def T(self, key: str) -> str:  # noqa: N802
        return self.strings.get(key, key)

    def F(self, key: str, *args) -> str:  # noqa: N802
        """Translated text with %s placeholders; tolerant of broken translations."""
        text = self.T(key)
        try:
            return text % args
        except (TypeError, ValueError):
            english = self.languages.english.get(key, key)
            try:
                return english % args
            except (TypeError, ValueError):
                return english

    def _app_icon(self) -> QIcon:
        for candidate in (Path(__file__).resolve().parent.parent / "icon.png",
                          self.assets / "icon.png", self.assets / "icon.ico"):
            if candidate.is_file():
                return QIcon(str(candidate))
        return QIcon.fromTheme(APP_ID)

    def _pixmap(self, name: str) -> QPixmap:
        return QPixmap(str(self.resources / name))

    # -- layout (MainForm.Designer.cs) ---------------------------------------

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        panel = QWidget()
        panel.setObjectName("panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setStyleSheet(
            "#panel { background:%s; }"
            " #panel QLabel { color:white; }"
            " #panel QGroupBox { color:white; font-weight:bold; border:1px solid #9cc3ec;"
            "   border-radius:4px; margin-top:10px; padding-top:6px; }"
            " #panel QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 3px; }"
            " #panel QLabel#modeLabel, #panel QLabel#dropLabel { background:%s; color:#0f172a;"
            "   border:1px solid #9cc3ec; border-radius:3px; }"
            " #panel QLineEdit { background:white; color:#111827; }"
            % (PANEL_BG, LIGHT_BG))
        root.addWidget(panel)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(11, 9, 11, 12)
        panel_layout.setSpacing(10)

        # Top: conversion group (left) + buttons (right)
        top = QHBoxLayout()
        top.setSpacing(12)
        panel_layout.addLayout(top)

        self.group = QGroupBox()
        self.group.setMinimumWidth(200)
        group_layout = QVBoxLayout(self.group)
        group_layout.setSpacing(8)
        self.mode_label = QLabel()
        self.mode_label.setObjectName("modeLabel")
        self.mode_label.setWordWrap(True)
        self.mode_label.setMinimumHeight(35)
        self.mode_label.setContentsMargins(4, 2, 4, 2)
        bold = QFont(self.mode_label.font())
        bold.setBold(True)
        self.mode_label.setFont(bold)
        group_layout.addWidget(self.mode_label)

        console_row = QHBoxLayout()
        self.console_label = QLabel()
        self.console_combo = QComboBox()
        self.console_combo.setFixedWidth(84)
        self.console_combo.currentIndexChanged.connect(self._on_console_changed)
        console_row.addWidget(self.console_label)
        console_row.addStretch(1)
        console_row.addWidget(self.console_combo)
        console_row.addSpacing(22)
        group_layout.addLayout(console_row)

        bitrate_row = QHBoxLayout()
        self.bitrate_label = QLabel()
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.setFixedWidth(84)
        self.bitrate_combo.currentIndexChanged.connect(self._on_bitrate_changed)
        self.bitrate_info = ClickableLabel()
        info = self._pixmap("info_bitrate.png")
        if not info.isNull():
            self.bitrate_info.setPixmap(info.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation))
        self.bitrate_info.setFixedSize(18, 18)
        self.bitrate_info.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bitrate_info.clicked.connect(self._show_bitrate_info)
        bitrate_row.addWidget(self.bitrate_label)
        bitrate_row.addStretch(1)
        bitrate_row.addWidget(self.bitrate_combo)
        bitrate_row.addSpacing(4)
        bitrate_row.addWidget(self.bitrate_info)
        group_layout.addLayout(bitrate_row)
        top.addWidget(self.group)

        right = QVBoxLayout()
        right.setSpacing(8)
        lang_row = QHBoxLayout()
        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(104)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.options_button = QPushButton()
        self.options_button.setMinimumWidth(72)
        self.options_button.clicked.connect(self._open_tool_options)
        lang_row.addWidget(self.language_combo, 1)
        lang_row.addWidget(self.options_button)
        right.addLayout(lang_row)

        self.convert_button = QPushButton()
        self.convert_button.setFixedSize(184, 64)
        big = QFont(self.convert_button.font())
        big.setPointSize(18)
        big.setBold(True)
        self.convert_button.setFont(big)
        self.convert_button.clicked.connect(self._on_convert)
        right.addWidget(self.convert_button)

        self.stop_button = QPushButton()
        self.stop_button.setFixedSize(184, 34)
        mid = QFont(self.stop_button.font())
        mid.setPointSize(12)
        mid.setBold(True)
        self.stop_button.setFont(mid)
        self.stop_button.clicked.connect(self._on_stop)
        right.addWidget(self.stop_button)
        right.addStretch(1)
        top.addLayout(right)

        # Console pictures
        pictures = QHBoxLayout()
        pictures.setSpacing(20)
        pictures.addStretch(1)
        self.console_pictures: dict[str, QLabel] = {}
        self.console_pixmaps: dict[str, tuple[QPixmap, QPixmap]] = {}
        for console, image in (("PSVita", "psvita.png"), ("PS4", "ps4.png"),
                               ("PS3", "ps3.png"), ("PSP", "psp.png")):
            label = QLabel()
            label.setFixedSize(76, 54)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pix = self._pixmap(image)
            if not pix.isNull():
                pix = pix.scaled(76, 54, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            grey = QIcon(pix).pixmap(pix.size(), QIcon.Mode.Disabled) if not pix.isNull() else pix
            self.console_pixmaps[console] = (pix, grey)
            self.console_pictures[console] = label
            pictures.addWidget(label)
        pictures.addStretch(1)
        panel_layout.addSpacing(20)
        panel_layout.addLayout(pictures)
        panel_layout.addSpacing(20)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setFrame(False)
        panel_layout.addWidget(self.path_edit)

        self.drop_label = ClickableLabel()
        self.drop_label.setObjectName("dropLabel")
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setMinimumHeight(31)
        self.drop_label.setCursor(Qt.CursorShape.PointingHandCursor)
        drop_font = QFont(self.drop_label.font())
        drop_font.setPointSize(13)
        drop_font.setBold(True)
        self.drop_label.setFont(drop_font)
        self.drop_label.clicked.connect(self._browse_file)
        panel_layout.addWidget(self.drop_label)

        # Status strip
        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.setStatusBar(status)
        self.status_label = QLabel()
        status_font = QFont(self.status_label.font())
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        self.status_label.setTextFormat(Qt.TextFormat.RichText)
        self.status_label.linkActivated.connect(self._open_link)
        status.addWidget(self.status_label, 1)
        self.tools_link = QLabel()
        self.tools_link.linkActivated.connect(lambda _url: self._show_tools_status())
        status.addPermanentWidget(self.tools_link)
        studio = QLabel('<a href="%s">BMK-Studio</a>' % STUDIO_URL)
        studio.setOpenExternalLinks(True)
        status.addPermanentWidget(studio)


    # -- small UI state helpers --------------------------------------------

    def set_status(self, key: str) -> None:
        self.status_key = key
        text = self.T(key).replace("&", "&amp;").replace("<", "&lt;")
        if key == "conversion_log_error":
            log = ConversionLog().command_log
            text += ' &nbsp;<a href="%s">%s</a>' % (QUrl.fromLocalFile(str(log)).toString(),
                                                     self.T("open_log"))
        self.status_label.setText(text)

    def _open_link(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _set_mode(self, mode: ConversionMode) -> None:
        self.mode = mode
        text = mode.display_text
        self.mode_label.setText(self.T("conversion_mode_prefix") + " " + text if text
                                else self.T("conversion_mode_empty"))

    def _update_console_images(self) -> None:
        selected = self.console if self.console_combo.isEnabled() else ""
        for console, label in self.console_pictures.items():
            active, grey = self.console_pixmaps[console]
            label.setPixmap(active if console == selected else grey)

    def _fill(self, combo: QComboBox, values: list[str], select_first: bool) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        combo.setCurrentIndex(0 if select_first and values else -1)
        combo.blockSignals(False)

    def _reset_ui(self) -> None:
        self.mode = ConversionMode.NONE
        self.input_file = None
        self.console = ""
        self.bitrate = ""
        self.tool_settings = ToolSettings()
        self._set_mode(ConversionMode.NONE)
        self.path_edit.clear()
        self._fill(self.console_combo, [], False)
        self._fill(self.bitrate_combo, [], False)
        self.console_combo.setEnabled(False)
        self.bitrate_combo.setEnabled(False)
        style_button(self.options_button, False)
        style_button(self.convert_button, False)
        if not self.player.is_active():
            style_button(self.stop_button, False)
        self._update_console_images()
        self.set_status("ready")

    # -- file loading (LoadDroppedFile) ---------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self.worker is None and event.mimeData().hasUrls() and any(
                u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None:
            return
        files = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if files:
            event.acceptProposedAction()
            self.load_file(Path(files[0]))

    def _browse_file(self) -> None:
        if self.worker is not None:
            return
        start = str(self.input_file.parent) if self.input_file else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, self.T("open_file_title"), start,
            "%s (*.wav *.mp3 *.at9 *.at3 *.WAV *.MP3 *.AT9 *.AT3);;* (*)" % self.T("audio_files"))
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path) -> None:
        self._reset_ui()
        kind = detect_input_kind(path)
        if not kind or not path.is_file():
            self.dialogs.ok(self.T("information"), self.T("invalid_audio_file"))
            self._reset_ui()
            return

        self.input_file = path
        self.path_edit.setText(str(path))
        self.path_edit.setCursorPosition(0)

        if kind in ("at9", "at3"):
            to_mp3 = self.dialogs.two_choice(self.T("format_question"), "MP3", "WAV")
            if kind == "at9":
                self._set_mode(ConversionMode.AT9_TO_MP3 if to_mp3 else ConversionMode.AT9_TO_WAV)
            else:
                self._set_mode(ConversionMode.AT3_TO_MP3 if to_mp3 else ConversionMode.AT3_TO_WAV)
            self._configure_consoles()
            question = "play_at9_question" if kind == "at9" else "play_at3_question"
            if self.dialogs.confirm(self.T(question)):
                self.player.play(path)
            return

        to_at9 = self.dialogs.two_choice(self.T("target_format_question"), "AT9", "AT3")
        if kind == "wav":
            self._set_mode(ConversionMode.WAV_TO_AT9 if to_at9 else ConversionMode.WAV_TO_AT3)
        else:
            self._set_mode(ConversionMode.MP3_TO_AT9 if to_at9 else ConversionMode.MP3_TO_AT3)
        self._configure_consoles()

    def _configure_consoles(self) -> None:
        self._fill(self.console_combo, consoles_for(self.mode), False)
        self._fill(self.bitrate_combo, [], False)
        self.console_combo.setEnabled(self.mode != ConversionMode.NONE)
        self.bitrate_combo.setEnabled(False)
        style_button(self.options_button, False)
        style_button(self.convert_button, False)
        self._update_console_images()

    # -- console / bitrate ----------------------------------------------------

    def _on_console_changed(self, index: int) -> None:
        if index < 0:
            return
        console = self.console_combo.currentText()
        if self.console and console != self.console:
            self.tool_settings = ToolSettings()  # SelectConsole resets on tool change
        self.console = console
        self._update_console_images()

        needs_bitrate = self.mode.needs_bitrate
        if needs_bitrate:
            self._fill(self.bitrate_combo, BITRATES[console], True)
            self.bitrate = self.bitrate_combo.currentText()
        else:
            self._fill(self.bitrate_combo, [], False)
            self.bitrate = ""
        self.bitrate_combo.setEnabled(needs_bitrate)
        style_button(self.options_button, True)
        style_button(self.convert_button, not needs_bitrate or bool(self.bitrate))

        if console == "PSVita" and needs_bitrate:
            self.dialogs.ok(self.T("information"), self.T("psvita_bitrate_info"))

    def _on_bitrate_changed(self, index: int) -> None:
        if index < 0:
            return
        self.bitrate = self.bitrate_combo.currentText()
        style_button(self.convert_button, True)

    def _show_bitrate_info(self) -> None:
        if not self.bitrate_combo.isEnabled() or self.console not in BitrateInfoDialog.IMAGES:
            return
        dialog = BitrateInfoDialog(self.console, self.resources, self.T("bitrate_info_title"), self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _open_tool_options(self) -> None:
        if not self.console or self.mode == ConversionMode.NONE:
            return
        dialog = ToolOptionsDialog(self.console, self.mode, self.tool_settings, self.T,
                                   self.resources, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.tool_settings = dialog.settings

    # -- conversion -----------------------------------------------------------

    def _current_job(self) -> Optional[ConversionJob]:
        if self.input_file is None or self.mode == ConversionMode.NONE or not self.console:
            return None
        return ConversionJob(mode=self.mode, input_file=self.input_file, console=self.console,
                             bitrate=self.bitrate if self.mode.needs_bitrate else "",
                             settings=self.tool_settings.clone())

    def _on_convert(self) -> None:
        job = self._current_job()
        if job is None or self.worker is not None:
            return
        if self.toolchain.needs_sony_tool(job) and not self.toolchain.has_tool(job.console):
            if not self._offer_tool_import(job.console):
                return
        for key in job.existing_output_warnings():
            if not self.dialogs.confirm(self.T(key)):
                return
        self._freeze()
        self.worker = ConversionThread(self.converter, job)
        self.worker.status.connect(self.set_status)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_done(self, result: ConversionResult) -> None:
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.wait()
        self._restore()

        if not result.succeeded:
            if result.error_key == "conversion_log_error":
                self.set_status("conversion_log_error")
                return
            if result.error_key == "tool_missing":
                self.set_status("ready")
                self._offer_tool_import(result.error_message or self.console)
                return
            message = self.T(result.error_key) if result.error_key else ""
            if result.error_message:
                message = (message + " " + result.error_message).strip()
            self.set_status("conversion_log_error")
            if message:
                self.dialogs.ok(self.T("error"), message, QMessageBox.Icon.Critical)
            return

        if result.ask_delete_wav and self.dialogs.confirm(self.T("delete_wav_question")):
            delete_intermediate(result)
        self.set_status("finish")
        if result.ask_play and result.output_file and self.dialogs.confirm(self.T("play_file_question")):
            self.player.play(result.output_file)

    def _freeze(self) -> None:
        widgets = [self.console_combo, self.bitrate_combo, self.language_combo, self.drop_label,
                   self.bitrate_info]
        self._frozen_state = {w: w.isEnabled() for w in widgets}
        self._frozen_state["convert"] = self.convert_button.isEnabled()
        self._frozen_state["options"] = self.options_button.isEnabled()
        for w in widgets:
            w.setEnabled(False)
        style_button(self.convert_button, False)
        style_button(self.options_button, False)
        self.setAcceptDrops(False)

    def _restore(self) -> None:
        for w, enabled in self._frozen_state.items():
            if isinstance(w, QWidget):
                w.setEnabled(enabled)
        style_button(self.convert_button, self._frozen_state.get("convert", False))
        style_button(self.options_button, self._frozen_state.get("options", False))
        self.setAcceptDrops(True)

    # -- user-supplied Sony tools ---------------------------------------------

    def _update_tools_link(self) -> None:
        missing = self.toolchain.missing_tools()
        label = self.T("sony_tools") + (" (%d/4)" % (4 - len(missing)) if missing else " ✓")
        self.tools_link.setText('<a href="#tools">%s</a>' % label)

    def _offer_tool_import(self, console: str) -> bool:
        """Ask to import the missing tool; True once it is installed."""
        if not self.dialogs.confirm(self.F("tools_import_question", console)):
            return False
        self._import_tools_dialog()
        return self.toolchain.has_tool(console)

    def _import_tools_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, self.T("tools_import_title"), str(Path.home()),
            "%s (*.exe *.EXE *.dll *.DLL);;* (*)" % self.T("windows_programs"))
        if not paths:
            return
        imported, skipped = import_tools([Path(p) for p in paths])
        lines = []
        if imported:
            lines.append(self.F("tools_imported", ", ".join(sorted(imported))))
        if skipped:
            lines.append(self.T("tools_not_recognized"))
            lines += ["  " + p.name for p in skipped]
        if lines:
            icon = QMessageBox.Icon.Information if imported else QMessageBox.Icon.Warning
            self.dialogs.ok(self.T("sony_tools"), "\n".join(lines), icon)
        self._update_tools_link()

    def _show_tools_status(self) -> None:
        installed = [c for c in TOOL_NAMES if self.toolchain.has_tool(c)]
        missing = [c for c in TOOL_NAMES if c not in installed]
        text = self.F("tools_status", ", ".join(installed) or self.T("none"),
                      ", ".join(missing) or self.T("none"))
        if self.dialogs.confirm(text):
            self._import_tools_dialog()

    # -- playback -------------------------------------------------------------

    def _wire_player(self) -> None:
        self.player.preparing.connect(lambda: (self.set_status("decoding_for_playback"),
                                               style_button(self.stop_button, True)))
        self.player.started.connect(lambda kind: self.set_status(
            "playing_at9" if kind == "at9" else "playing_at3"))
        self.player.stopped.connect(self._on_playback_stopped)
        self.player.error.connect(self._on_playback_error)

    def _on_playback_stopped(self) -> None:
        style_button(self.stop_button, False)
        if self.worker is None:
            self.set_status("stop")

    def _on_playback_error(self, key: str, detail: str) -> None:
        style_button(self.stop_button, False)
        text = self.T(key) if key != "playback_error" else self.T("playback_error")
        self.dialogs.ok(self.T("error"), (text + " " + detail).strip(), QMessageBox.Icon.Critical)

    def _on_stop(self) -> None:
        self.player.stop()

    # -- language -------------------------------------------------------------

    def _load_language_list(self) -> None:
        saved = str(self.settings_store.value("language", "en"))
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        for item in self.languages.available():
            self.language_combo.addItem(item.name, item.code)
        index = self.language_combo.findData(saved)
        if index < 0:
            index = max(0, self.language_combo.findData("en"))
        self.language_combo.setCurrentIndex(index)
        self.language_combo.blockSignals(False)
        self._on_language_changed(index)

    def _on_language_changed(self, index: int) -> None:
        code = self.language_combo.itemData(index) if index >= 0 else "en"
        code = code or "en"
        self.strings, outdated = self.languages.load(code)
        self.settings_store.setValue("language", code)
        self._apply_language()
        if outdated and code not in self.warned_outdated:
            self.warned_outdated.add(code)
            self.dialogs.ok(self.T("information"), self.T("language_outdated_warning"),
                            QMessageBox.Icon.Warning)

    def _apply_language(self) -> None:
        self.drop_label.setText(self.T("drag_drop"))
        self.convert_button.setText(self.T("convert"))
        self.options_button.setText(self.T("tool_options"))
        self.stop_button.setText(self.T("stop_playing"))
        self.group.setTitle(self.T("conversion_type"))
        self.bitrate_label.setText(self.T("bitrate"))
        self.console_label.setText(self.T("console_type"))
        tips = {self.group: "tooltip_conversion_type", self.console_label: "tooltip_console_type",
                self.bitrate_info: "tooltip_bitrate_info", self.bitrate_label: "tooltip_bitrate",
                self.bitrate_combo: "tooltip_bitrate", self.console_combo: "tooltip_console_type",
                self.drop_label: "tooltip_drag_drop_click", self.convert_button: "tooltip_convert",
                self.path_edit: "tooltip_file_path", self.options_button: "tooltip_tool_options"}
        for widget, key in tips.items():
            widget.setToolTip(_wrap(self.T(key)))
        self._set_mode(self.mode)
        self.set_status(self.status_key)
        self._update_tools_link()

    # -- shutdown -------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        self.player.shutdown()
        if self.worker is not None:
            self.worker.wait()
        super().closeEvent(event)


def run_gui(argv: list[str]) -> int:
    app = QApplication([sys.argv[0]] + [a for a in argv if a.startswith("-")])
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)
    files = [a for a in argv if not a.startswith("-")]
    window = MainWindow(files[0] if files else None)
    window.show()
    return app.exec()
