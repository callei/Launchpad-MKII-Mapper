import sys
import subprocess
import os
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QPushButton, QVBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QHBoxLayout, QFileDialog, QMessageBox, QDialog, QComboBox,
    QLineEdit, QColorDialog, QDialogButtonBox, QInputDialog, QTabWidget, QSpinBox, QFileIconProvider,
    QCheckBox
)
from PySide6.QtCore import Qt, Signal, QPoint, QTimer, QThread
from PySide6.QtGui import QPalette, QColor, QFont, QFontDatabase, QPainterPath, QRegion, QPainter, QPen
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleFactory
from PySide6.QtWidgets import QGraphicsDropShadowEffect
import yaml
import re
import traceback
import shutil

"""Main GUI module for Launchpad Mapper.

Release build: verbose startup logging removed. To enable lightweight
debug logging create an environment variable LAUNCHPADMAPPER_DEBUG=1
or launch the app with the --debug flag. When enabled (and frozen),
log lines are written to %APPDATA%/LaunchpadMapper/startup.log.
"""

DEBUG_MODE = ('--debug' in sys.argv) or os.environ.get('LAUNCHPADMAPPER_DEBUG') == '1'
if '--debug' in sys.argv:
    try:
        sys.argv.remove('--debug')
    except ValueError:
        pass

_LOG_PATH = None
def _debug_log(msg: str):
    """Write a line to the debug log if debug mode is active."""
    global _LOG_PATH
    if not DEBUG_MODE:
        return
    try:
        if _LOG_PATH is None:
            _LOG_PATH = Path(os.environ.get('APPDATA', Path.home())) / 'LaunchpadMapper' / 'startup.log'
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, 'w', encoding='utf-8') as f:
                f.write('LaunchpadMapper debug log (fresh session)\n')
                f.write(f'Python: {sys.version}\n')
                f.write(f'cwd={os.getcwd()}\n')
        with open(_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(msg.rstrip() + '\n')
    except Exception:
        pass

"""Launchpad Mapper GUI.

Fixes applied:
- Ensures parent project directory is on sys.path so the backend import works when running `python gui/launchpad_mapper.py`.
- Adds color-only pad mapping (assign a color without an action).
"""

# Ensure project root is on sys.path for direct script execution
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# MIDI/Launchpad integration
try:
    from launchpad.controller import LaunchpadController
except ImportError:
    LaunchpadController = None

# AppData-konfigurationsplats (Windows). Fallback: projektrot.
APPDATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LaunchpadMapper"
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APPDATA_DIR / "config.yaml"
PRESETS_DIR = APPDATA_DIR / "presets"

class PadButton(QPushButton):
    """GUI representation of a Launchpad pad (matrix or control)."""
    def __init__(self, x, y, label="", color=None, control=False):
        super().__init__(label)
        self.x = x
        self.y = y
        self.control = control  # True for top/side buttons
        self._base_color = color  # hex string or None
        self._pressed = False
        size = 30 if control else 40
        self.setFixedSize(size, size)
        self.refresh_style()

    def set_pressed(self, pressed: bool):
        self._pressed = pressed
        self.refresh_style()

    def update_color(self, color):
        self._base_color = color
        self.refresh_style()

    def refresh_style(self):
        bg = self._base_color if self._base_color else 'transparent'
        border_w = 3 if self._pressed else 1
        radius = 15 if self.control else 4
        self.setStyleSheet(
            f"background-color: {bg}; border: {border_w}px solid #555; border-radius:{radius}px;"
        )

class FramelessPopup(QDialog):
    """Reusable frameless popup with a custom title bar matching the main window style."""
    def __init__(self, title: str = "", parent=None, allow_minimize: bool = False):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        # Enable transparent corners so mask + rounded visuals show properly
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._drag_pos: QPoint | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # Title bar
        self._title_bar = QWidget()
        self._title_bar.setObjectName("titleBar")  # Popup title bar keeps rounded corners via stylesheet
        tb_layout = QHBoxLayout(self._title_bar)
        tb_layout.setContentsMargins(8, 4, 8, 4)
        tb_layout.setSpacing(6)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("titleLabel")
        self._title_label.setStyleSheet("color:#ddd; font-weight:500;")
        tb_layout.addWidget(self._title_label)
        tb_layout.addStretch(1)
        btn_style = (
            "QPushButton { border:none; background:#333; color:#ccc; padding:4px 10px; border-radius:4px; }"
            "QPushButton:hover { background:#3f3f3f; color:#fff; }"
            "QPushButton:pressed { background:#222; }"
        )
        if allow_minimize:
            min_btn = QPushButton("–")
            min_btn.setFixedWidth(26)
            min_btn.setStyleSheet(btn_style)
            min_btn.clicked.connect(self.showMinimized)
            tb_layout.addWidget(min_btn)
        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(26)
        close_btn.setStyleSheet(btn_style + "QPushButton { color:#e66; } QPushButton:hover { background:#a33; color:#fff; }")
        close_btn.clicked.connect(self.reject)
        tb_layout.addWidget(close_btn)
        self._title_bar.mousePressEvent = self._title_mouse_press  # type: ignore
        self._title_bar.mouseMoveEvent = self._title_mouse_move    # type: ignore
        self._title_bar.mouseReleaseEvent = self._title_mouse_release  # type: ignore
        # Content wrapper to give rounded corners below title bar
        self._content_frame = QWidget()
        self._content_frame.setObjectName("popupBody")
        self._content_layout = QVBoxLayout(self._content_frame)
        self._content_layout.setContentsMargins(14, 12, 14, 14)
        self._content_layout.setSpacing(10)
        root.addWidget(self._title_bar)
        root.addWidget(self._content_frame, 1)

    def resizeEvent(self, event):  # Ensure whole popup has rounded shape (no black square behind)
        super().resizeEvent(event)
        try:
            radius = 10
            path = QPainterPath()
            path.addRoundedRect(self.rect(), radius, radius)
            region = QRegion(path.toFillPolygon().toPolygon())
            self.setMask(region)
        except Exception:
            pass

    def set_popup_title(self, text: str):
        self._title_label.setText(text)

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    # Drag support
    def _title_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _title_mouse_move(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _title_mouse_release(self, event):
        self._drag_pos = None
        event.accept()


class ColorPickerPopup(FramelessPopup):
    """Custom color picker wrapped in our frameless popup to maintain consistent styling."""
    def __init__(self, initial_color: str | None, original_color: str | None, parent=None):
        super().__init__(title="Pick Color", parent=parent)
        self._selected: str | None = initial_color
        self._original = original_color
        lay = self.content_layout()
        # Embed a standard QColorDialog (non-native) without its own window frame
        self._dlg = QColorDialog(QColor(initial_color or '#ffffff'), self)
        self._dlg.setOption(QColorDialog.DontUseNativeDialog, True)
        # Remove window frame styling conflicts
        self._dlg.setWindowFlags(Qt.Widget)
        host = QWidget()
        host.setObjectName("colorPickerHost")
        h_layout = QVBoxLayout(host)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.addWidget(self._dlg)
        lay.addWidget(host)
        # Extra buttons row
        btn_row = QHBoxLayout()
        self.clear_btn = QPushButton("Clear")
        self.store_current_btn = QPushButton("Store Color → Slot…")
        # Egna tydliga OK/Cancel-knappar (de inbyggda tas inte längre bort/flyttas dynamiskt)
        self.ok_btn = QPushButton("OK")
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.clear_btn)
        btn_row.addWidget(self.store_current_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)
        self._btn_row = btn_row  # kvar för bakåtkompabilitet (om vi senare vill återanvända relocation)
        self.clear_btn.clicked.connect(self._clear_color)
        self.store_current_btn.clicked.connect(self._store_current)
        self.ok_btn.clicked.connect(self._accept)
        self.cancel_btn.clicked.connect(self.reject)
        # För säkerhet: fånga dubbelklick i färgrutan osv
        self._dlg.accepted.connect(self._accept)
        self._dlg.rejected.connect(self.reject)
        # Ta bort &-mnemonics
        QTimer.singleShot(0, self._strip_mnemonics)
        # Ingen dynamisk flytt av interna knappar längre (egna knappar används)
        QTimer.singleShot(0, self._hide_internal_dialog_buttons)

    def _hide_internal_dialog_buttons(self):
        """Döljer inbyggda OK/Cancel i QColorDialog så bara våra egna syns."""
        from PySide6.QtWidgets import QDialogButtonBox
        box = self._dlg.findChild(QDialogButtonBox)
        if box:
            box.hide()

    def _strip_mnemonics(self):
        from PySide6.QtWidgets import QLabel, QPushButton
        widgets = self._dlg.findChildren(QLabel) + self._dlg.findChildren(QPushButton)
        for w in widgets:
            txt = w.text()
            if not txt:
                continue
            placeholder = "__AMP__"
            txt = txt.replace("&&", placeholder)
            if '&' in txt:
                txt = txt.replace('&', '')
            txt = txt.replace(placeholder, '&')
            if txt != w.text():
                w.setText(txt)

    def _relocate_dialog_buttons(self):
        from PySide6.QtWidgets import QDialogButtonBox
        box = self._dlg.findChild(QDialogButtonBox)
        if not box:
            return
        ok = box.button(QDialogButtonBox.Ok)
        cancel = box.button(QDialogButtonBox.Cancel)
        # Reparent and add to row in desired order (OK then Cancel)
        if ok:
            ok.setParent(self)
            self._btn_row.addWidget(ok)
        if cancel:
            cancel.setParent(self)
            self._btn_row.addWidget(cancel)
        box.hide()

    def _clear_color(self):
        self._selected = None
        self.accept()

    def _store_current(self):
        col = self._dlg.currentColor()
        if not col.isValid():
            return
        slot, ok = QInputDialog.getInt(self, "Custom Color Slot", "Slot (1-16):", 1, 1, 16, 1)
        if ok:
            QColorDialog.setCustomColor(slot-1, col)

    def _accept(self):
        col = self._dlg.currentColor()
        if col.isValid():
            self._selected = col.name()
        self.accept()

    def get_selected_color(self) -> str | None:
        return self._selected


class TextInputPopup(FramelessPopup):
    """Frameless stilad textinmatningsruta (ersätter QInputDialog.getText)."""
    def __init__(self, title: str, label: str, default: str = "", parent=None, placeholder: str | None = None):
        super().__init__(title=title, parent=parent)
        lay = self.content_layout()
        self._edit = QLineEdit()
        self._edit.setText(default)
        if placeholder:
            self._edit.setPlaceholderText(placeholder)
        lay.addWidget(QLabel(label))
        lay.addWidget(self._edit)
        btn_row = QHBoxLayout()
        self.ok_btn = QPushButton("OK")
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addStretch(1)
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        self._edit.returnPressed.connect(self.accept)
        QTimer.singleShot(0, self._post_init_focus)
        self._result: str | None = None

    def _post_init_focus(self):
        self._edit.setFocus()
        self._edit.selectAll()

    def get_text(self) -> tuple[str, bool]:
        if self.exec() == QDialog.Accepted:
            return self._edit.text(), True
        return "", False


class PadEditorDialog(FramelessPopup):
    def __init__(self, pad, mapping, parent=None, animation_names=None):
        super().__init__(title=f"Edit Pad ({pad[0]},{pad[1]})", parent=parent)
        self.pad = pad
        self.mapping = mapping
        # Use content layout of frameless popup
        layout = self.content_layout()
        # Action type
        self.action_type = QComboBox()
        # Display names are space-based; underlying stored mapping types are also space-based (legacy underscore forms normalized on load)
        self.action_type.addItems([
            "none",
            "color only",
            "run process",
            "launch app",
            "hotkey",
            "media control",
            "switch layer",
            "layer nav",
            "animation"
        ])
        layout.addWidget(QLabel("Action Type:"))
        layout.addWidget(self.action_type)
        # Value (command / process)
        self.action_value = QLineEdit()
        self.value_label = QLabel("Action Value / Message / Command:")
        layout.addWidget(self.value_label)
        layout.addWidget(self.action_value)
        # Launch app path + browse + pick installed
        self.launch_label = QLabel("Application Path (launch app):")
        self.launch_path = QLineEdit()
        self.launch_browse_btn = QPushButton("Browse…")
        self.launch_pick_btn = QPushButton("Pick Installed…")
        launch_row = QHBoxLayout()
        launch_row.addWidget(self.launch_path)
        launch_row.addWidget(self.launch_browse_btn)
        launch_row.addWidget(self.launch_pick_btn)
        layout.addWidget(self.launch_label)
        lrw = QWidget(); lrw.setLayout(launch_row)
        layout.addWidget(lrw)
        self.launch_browse_btn.clicked.connect(self._browse_launch_app)
        self.launch_pick_btn.clicked.connect(self._pick_installed_app)
        # Hotkey edit
        self.hotkey_label = QLabel("Hotkey (ex: ctrl+shift+esc eller sekvens: ctrl+alt+del>win+d):")
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setPlaceholderText("ctrl+shift+esc  |  win+d  |  ctrl+alt+del>win+d")
        layout.addWidget(self.hotkey_label)
        layout.addWidget(self.hotkey_edit)
        # Media control select
        self.media_label = QLabel("Media Control Action:")
        self.media_select = QComboBox()
        self.media_select.addItems(["play/pause", "next", "previous", "volume up", "volume down", "mute"])
        layout.addWidget(self.media_label)
        layout.addWidget(self.media_select)
        # Switch layer
        self.layer_select = QLineEdit()
        self.layer_label = QLabel("Layer (for switch layer):")
        layout.addWidget(self.layer_label)
        layout.addWidget(self.layer_select)
        # Layer navigation
        self.nav_label = QLabel("Layer Nav (for layer nav):")
        self.nav_select = QComboBox()
        self.nav_select.addItems(["prev", "next"])
        layout.addWidget(self.nav_label)
        layout.addWidget(self.nav_select)
        # Animation fields
        self.animation_label = QLabel("Animation:")
        self.animation_select = QComboBox()
        layout.addWidget(self.animation_label)
        layout.addWidget(self.animation_select)
        # Animation iterations only (0=infinite, 1=once [default])
        loop_iter_row = QHBoxLayout()
        self.animation_loop_iterations = QSpinBox()  # Changed to allow for 0 iterations
        self.animation_loop_iterations.setRange(0, 999)
        self.animation_loop_iterations.setValue(1)
        loop_iter_row.addWidget(QLabel("Iterations (0 = infinite):"))
        loop_iter_row.addWidget(self.animation_loop_iterations)
        self.animation_loop_container = QWidget()
        self.animation_loop_container.setLayout(loop_iter_row)
        layout.addWidget(self.animation_loop_container)
        # Color button opens enhanced color dialog (includes Use Last / Clear)
        self.color_btn = QPushButton("Pick Color")
        self.color_btn.clicked.connect(self.pick_color)
        layout.addWidget(self.color_btn)
        self.selected_color = None
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)
        # Populate animations
        if animation_names:
            self.animation_select.addItems(animation_names)
        # Hide context-specific inputs by default; shown via _on_type_changed
        for w in (
            self.value_label,
            self.action_value,
            self.launch_label,
            self.launch_path,
            lrw,
            self.hotkey_label,
            self.hotkey_edit,
            self.media_label,
            self.media_select,
            self.layer_label,
            self.layer_select,
            self.nav_label,
            self.nav_select,
            self.animation_label,
            self.animation_select,
            self.animation_loop_container,
        ):
            w.setVisible(False)
        # Pre-fill existing mapping
        if mapping:
            # Normalize legacy underscore types to space versions for the editor
            mtype = mapping.get("type", "none")
            legacy_map = {"layer_nav": "layer nav", "switch_layer": "switch layer", "run_process": "run process"}
            mtype = legacy_map.get(mtype, mtype)
            if mtype == "color":
                self.action_type.setCurrentText("color only")
            elif mtype == "print":  # legacy mapping type; treat as none
                self.action_type.setCurrentText("none")
            else:
                self.action_type.setCurrentText(mtype)
            self.action_value.setText(str(mapping.get("message", mapping.get("command", ""))))
            if mtype == "launch app":
                self.launch_path.setText(str(mapping.get("path", "")))
            if mtype == "hotkey":
                self.hotkey_edit.setText(str(mapping.get("keys", "")))
            if mtype == "media control":
                act = mapping.get("action", "play/pause")
                idx = self.media_select.findText(act)
                if idx >= 0:
                    self.media_select.setCurrentIndex(idx)
            self.layer_select.setText(str(mapping.get("layer", "")))
            self.selected_color = mapping.get("color", None)
            if mtype in ("layer nav", "layer_nav"):
                self.nav_label.setVisible(True)
                self.nav_select.setVisible(True)
                nav = mapping.get("nav", "prev")
                idx = self.nav_select.findText(nav)
                if idx >= 0:
                    self.nav_select.setCurrentIndex(idx)
            if mtype == "animation":
                name = mapping.get("name", "")
                if name and name in [self.animation_select.itemText(i) for i in range(self.animation_select.count())]:
                    idx = self.animation_select.findText(name)
                    if idx >= 0:
                        self.animation_select.setCurrentIndex(idx)
                self.animation_label.setVisible(True)
                self.animation_select.setVisible(True)
                self.animation_loop_container.setVisible(True)
                self.animation_loop_iterations.setValue(mapping.get("iterations", 0))  # Allow for 0 iterations
        # Hook type change
        self.action_type.currentTextChanged.connect(self._on_type_changed)
        # Ensure visibility consistent with current type
        self._on_type_changed(self.action_type.currentText())

    def _on_type_changed(self, text):
        # Normalize text just in case (legacy underscores)
        legacy_map = {"layer_nav": "layer nav", "switch_layer": "switch layer", "run_process": "run process"}
        text_norm = legacy_map.get(text, text)
        is_anim = (text_norm == "animation")
        self.animation_label.setVisible(is_anim)
        self.animation_select.setVisible(is_anim)
        self.animation_loop_container.setVisible(is_anim)
        is_nav = (text_norm == "layer nav")
        self.nav_label.setVisible(is_nav)
        self.nav_select.setVisible(is_nav)
        is_run = (text_norm == "run process")
        self.value_label.setVisible(is_run)
        self.action_value.setVisible(is_run)
        is_launch = (text_norm == "launch app")
        self.launch_label.setVisible(is_launch)
        self.launch_path.setVisible(is_launch)
        # The container widget lrw
        try:
            self.launch_path.parentWidget().setVisible(is_launch)
        except Exception:
            pass
        is_hotkey = (text_norm == "hotkey")
        self.hotkey_label.setVisible(is_hotkey)
        self.hotkey_edit.setVisible(is_hotkey)
        is_media = (text_norm == "media control")
        self.media_label.setVisible(is_media)
        self.media_select.setVisible(is_media)
        is_switch = (text_norm == "switch layer")
        self.layer_label.setVisible(is_switch)
        self.layer_select.setVisible(is_switch)
        # Adjust size so the dialog can shrink when controls hide
        try:
            # Nollställ minima för att tillåta krympning
            self.setMinimumSize(0, 0)
            self.setMinimumHeight(0)
            self.resize(self.sizeHint())
            self.adjustSize()
            # För säkerhet kör en deferred justering (Qt kan behöva layout-pass)
            QTimer.singleShot(0, self.adjustSize)
        except Exception:
            pass

    def pick_color(self):
        picker = ColorPickerPopup(initial_color=self.selected_color, original_color=self.selected_color, parent=self)
        if picker.exec() == QDialog.Accepted:
            self.selected_color = picker.get_selected_color()

    # Legacy color menu removed (function kept no-op if called)
    def _open_color_menu(self):
        self.pick_color()

    def _browse_launch_app(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Application", str(Path.home()))
        if path:
            self.launch_path.setText(path)
    def _pick_installed_app(self):
        dlg = AppPickerDialog(self)
        if dlg.exec() == QDialog.Accepted:
            sel = dlg.get_selected()
            if sel:
                self.launch_path.setText(sel)

class _AppScanWorker(QThread):
    resultReady = Signal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
    def stop(self):
        self._stop = True
    def run(self):
        try:
            if sys.platform != 'win32':
                self.resultReady.emit([])
                return
            exts = {'.lnk', '.exe'}
            entries: list[tuple[str,str,str|None,bool]] = []
            seen_effective = set()

            def add_entry(label: str, full: str, target: str | None, is_link: bool):
                if not label:
                    return
                eff = target or full
                # Basic sanity on path
                if not eff or not eff.lower().endswith('.exe'):
                    return
                key = (label.lower(), eff.lower())
                if key in seen_effective:
                    return
                seen_effective.add(key)
                entries.append((label, full, target, is_link))

            # --- 1. Startmeny ---
            dirs = []
            pd = os.environ.get('PROGRAMDATA')
            if pd:
                d = Path(pd)/'Microsoft/Windows/Start Menu/Programs'
                if d.exists():
                    dirs.append(d)
            ra = os.environ.get('APPDATA')
            if ra:
                d = Path(ra)/'Microsoft/Windows/Start Menu/Programs'
                if d.exists():
                    dirs.append(d)
            # Optional .lnk resolution
            try:
                import win32com.client  # type: ignore
                shell = win32com.client.Dispatch('WScript.Shell')
            except Exception:
                shell = None
            skip_sub = ("uninstall", "update", "help", "readme", "license", "manual", "guide", "support", "about", "documentation")
            for root in dirs:
                for path in root.rglob('*'):
                    if self._stop:
                        return
                    if not path.is_file():
                        continue
                    suf = path.suffix.lower()
                    if suf not in exts:
                        continue
                    name_lower = path.stem.lower()
                    if any(s in name_lower for s in skip_sub):
                        continue
                    full = str(path)
                    target = None
                    is_link = (suf == '.lnk')
                    if is_link and shell:
                        try:
                            sc = shell.CreateShortcut(full)
                            target = sc.TargetPath or None
                        except Exception:
                            target = None
                    label = path.stem
                    add_entry(label, full, target, is_link)

            # --- 2. Registry Uninstall (Control Panel list) ---
            import winreg
            uninstall_roots = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            for root, sub in uninstall_roots:
                try:
                    with winreg.OpenKey(root, sub) as hk:
                        i = 0
                        while True:
                            if self._stop:
                                return
                            try:
                                skn = winreg.EnumKey(hk, i)
                            except OSError:
                                break
                            i += 1
                            try:
                                with winreg.OpenKey(hk, skn) as sk:
                                    try:
                                        name, _ = winreg.QueryValueEx(sk, 'DisplayName')
                                    except OSError:
                                        continue
                                    if not name or len(name.strip()) < 2:
                                        continue
                                    # Try InstallLocation / DisplayIcon
                                    target_path = None
                                    for val in ('DisplayIcon', 'InstallLocation'):
                                        try:
                                            v, _ = winreg.QueryValueEx(sk, val)
                                            if isinstance(v, str) and v.strip():
                                                target_path = v.strip()
                                                break
                                        except OSError:
                                            continue
                                    if target_path:
                                        # Normalize possible "C:\path\app.exe,0" icon spec
                                        t = target_path.split(',')[0].strip().strip('"')
                                        if os.path.isdir(t):
                                            # Heuristik: leta efter en exe med liknande namn i katalogen
                                            base_try = name.lower().replace(' ', '')
                                            candidates = []
                                            try:
                                                for p in Path(t).glob('*.exe'):
                                                    candidates.append(p)
                                            except Exception:
                                                pass
                                            pick = None
                                            for p in candidates:
                                                if base_try and base_try in p.stem.lower().replace(' ', ''):
                                                    pick = p; break
                                            if not pick and candidates:
                                                # Ta största exe som fallback
                                                pick = max(candidates, key=lambda p: p.stat().st_size if p.exists() else 0)
                                            if pick:
                                                t = str(pick)
                                            else:
                                                t = ''
                                        if t.lower().endswith('.exe'):
                                            add_entry(name, t, t, False)
                                    # else: skip entries without path
                            except Exception:
                                continue
                except Exception:
                    continue

            # --- 3. Fallback: Program Files rot (ta top-level exes i varje underkatalog) om vi fortfarande har väldigt få ---
            if len(entries) < 5:
                roots = []
                for env in ('ProgramFiles', 'ProgramFiles(x86)'):
                    p = os.environ.get(env)
                    if p and os.path.isdir(p):
                        roots.append(Path(p))
                for root in roots:
                    if self._stop:
                        return
                    try:
                        for subdir in root.iterdir():
                            if self._stop:
                                return
                            if not subdir.is_dir():
                                continue
                            # Hitta kandidater i direkt-rot (inte djupt) för fart
                            exes = list(subdir.glob('*.exe'))
                            if not exes:
                                continue
                            # Heuristik: välj största exe
                            pick = max(exes, key=lambda p: p.stat().st_size if p.exists() else 0)
                            label = subdir.name
                            add_entry(label, str(pick), str(pick), False)
                            if len(entries) > 60:  # begränsa fallback-volym
                                break
                    except Exception:
                        continue

            entries.sort(key=lambda t: t[0].lower())
            self.resultReady.emit(entries)
        except Exception:
            self.resultReady.emit([])

class AppPickerDialog(QDialog):
    """Lista installerade program (rensad) med Start-menyn som källa.
    Visar bara körbara .exe (direkt eller via .lnk) och filtrerar bort uppenbara verktyg/avinstallationsgenvägar.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Installed Application")
        self.resize(560, 540)
        v = QVBoxLayout(self)
        top_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter… (skriv för att filtrera)")
        self.show_all_cb = QCheckBox("Visa allt")
        top_row.addWidget(self.filter_edit)
        top_row.addWidget(self.show_all_cb)
        v.addLayout(top_row)
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        self.status_lbl = QLabel("Skannar Start-menyn…")
        v.addWidget(self.status_lbl)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(btns)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        self.filter_edit.textChanged.connect(self._refilter)
        self.show_all_cb.toggled.connect(self._refilter)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        self._raw_entries = []  # (label, full, target, is_link)
        self._all_items: list[tuple[str,str]] = []  # (label, effective_path)
        self._worker = _AppScanWorker(self)
        self._worker.resultReady.connect(self._on_scan_done)
        self._worker.start()
        QTimer.singleShot(0, self.filter_edit.setFocus)

    def _on_scan_done(self, entries):
        self._raw_entries = entries
        self.status_lbl.setText(f"Hittade {len(entries)} poster. Förfinar…")
        self._prepare_items()
        self.status_lbl.setText(f"Visar {len(self._all_items)} program")
        self._refilter()

    def _prepare_items(self):
        cleaned = []
        allowed_sys = {"notepad.exe", "mspaint.exe", "calc.exe"}
        prog_dirs = [p.lower() for p in [os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')] if p]
        for label, full, target, is_link in self._raw_entries:
            eff = (target or full)
            if not eff:
                continue
            if not eff.lower().endswith('.exe'):
                # Skip non-exe targets
                continue
            low = eff.lower()
            # Filter heuristics (unless show_all later)
            if not any(low.startswith(pd) for pd in prog_dirs):
                # Allow some common system tools
                if os.path.basename(low) not in allowed_sys:
                    # Deprioritize random system32 stuff; skip by default
                    if 'system32' in low:
                        continue
            base = os.path.basename(low)
            if any(x in base.lower() for x in ("unins", "uninstall", "setup", "update", "crash", "debug")):
                continue
            cleaned.append((label, eff))
        # Remove dups by effective path keeping first label
        seen = set()
        unique = []
        for label, eff in cleaned:
            if eff in seen:
                continue
            seen.add(eff)
            unique.append((label, eff))
        unique.sort(key=lambda t: t[0].lower())
        self._all_items = unique

    def _refilter(self):
        text = self.filter_edit.text().strip().lower()
        show_all = self.show_all_cb.isChecked()
        items = self._all_items if not show_all else [
            (l, (t or f) if (t or f) else f) for (l, f, t, is_link) in self._raw_entries
            if (t or f) and (t or f).lower().endswith('.exe')
        ]
        if text:
            items = [p for p in items if text in p[0].lower() or text in os.path.basename(p[1]).lower()]
        self._rebuild_list(items)
        self.status_lbl.setText(f"Visar {len(items)} program")

    def _rebuild_list(self, items):
        self.list.clear()
        for label, full in items:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, full)
            self.list.addItem(it)

    def get_selected(self) -> str | None:
        it = self.list.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

class MainWindow(QMainWindow):
    pad_pressed = Signal(int, int)   # x, y
    pad_released = Signal(int, int)
    start_animation_requested = Signal(str, int, bool)  # name, iterations, preview
    def sync_pad_lights(self):
        if not hasattr(self, 'lp') or self.lp is None:
            return
        # If animations are active, avoid clearing everything to reduce flicker.
        anim_active = bool(getattr(self, '_anim_runs', []))
        composite = getattr(self, '_anim_composite_prev', {}) if anim_active else {}
        if not anim_active:
            # Clear grid notes
            self.lp.clear()
            # Explicitly clear top row & side column
            for x in range(8):
                try:
                    self.lp.set_pad_color((x, -1), (0,0,0))
                except Exception:
                    pass
            for y in range(8):
                try:
                    self.lp.set_pad_color((8, y), (0,0,0))
                except Exception:
                    pass
        # Apply base mapping to all pads, but don't overwrite pads currently set by animations
        def _set_base(pad_xy, rgb):
            if pad_xy in composite:
                return
            try:
                self.lp.set_pad_color(pad_xy, rgb)
            except Exception:
                pass
        # Top row and side column
        for x in range(8):
            _set_base((x, -1), self._get_base_color((x, -1)))
        for y in range(8):
            _set_base((8, y), self._get_base_color((8, y)))
        # Grid
        for y in range(8):
            for x in range(8):
                _set_base((x, y), self._get_base_color((x, y)))
    def __init__(self, start_hidden: bool=False):
        super().__init__()
        self.theme_name = "Minimalistic Black"
        # Frameless window for a slick look
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setWindowTitle("")  # Remove default title text (taskbar icon still shows app name)
        self.resize(860, 540)
        self._drag_pos: QPoint | None = None
        # Start with an empty layout (user can load preset/config)
        # Include default layer navigation buttons (top: x=2 back, x=3 forward)
        self.config = {"active_layer": "main", "layers": {"main": {"pads": {
            "2,-1": {"type": "layer nav", "nav": "prev", "color": "#2980b9"},
            "3,-1": {"type": "layer nav", "nav": "next", "color": "#8e44ad"}
        }}}, "animations": {}}
        self.active_layer = "main"
        self.layers = self.config["layers"]
        self.animations = self.config.get("animations", {})  # name -> {frames:[{duration:int, pads:{"x,y":"#rrggbb"}}]}
        self.current_animation = None
        self.current_frame_index = 0
        # Animation playback scheduler (supports concurrent runs)
        self._anim_runs = []  # list of dicts: {name, frames, index, time_left, iterations(None|int), started_seq}
        self._anim_composite_prev = {}  # pad(tuple)->(r,g,b)
        self._anim_timer_active = False
        self._anim_start_seq = 0
        self.current_preset_name = None  # Track which preset is currently loaded
        self.lp = None
        # Internal dirty tracking for debounced autosave
        self._dirty = False
        self._save_debounce_timer = None
        # Presets support
        self.ensure_presets_dir()
        self.create_initial_preset_if_missing()
        # Attempt to load existing config (including animations) before building UI
        self._load_config_on_startup()
        # Normalize any legacy mapping type names
        self._normalize_all_mappings()
        # Flag to suppress autosave during initial construction
        self._ready = False
        # Connect signals for thread-safe UI updates
        self.pad_pressed.connect(self.animate_pad_press)
        self.pad_released.connect(self.animate_pad_release)
        # Ensure animations start on GUI thread
        self.start_animation_requested.connect(self._start_animation_from_signal)
        self.custom_font_family = None
        self.last_color = None  # Track last used color for dialogs
        self.load_custom_font()
        self.init_ui()
        self.apply_theme()
        self._install_tray()
        # Always start with preset 'empty' if it exists (override layer contents at launch)
        from pathlib import Path as _P
        empty_path = PRESETS_DIR / "empty.yaml"
        if empty_path.exists():
            try:
                self.load_preset("empty")
            except Exception:
                pass
        # Mark ready so subsequent edits trigger autosave
        self._ready = True
        # Try automatic Launchpad connect after UI shown
        QTimer.singleShot(0, lambda: self.init_midi(auto=True))
        # Start hidden om flagga givits
        if start_hidden:
            self.hide()

    # ---- Custom Title Bar Support ----
    def _build_title_bar(self):
        bar = QWidget()
        bar.setObjectName("mainTitleBar")  # Different object name so we can style separately (no rounded corners)
        layout = QHBoxLayout()
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)
        title_lbl = QLabel("Launchpad Mapper")
        title_lbl.setObjectName("titleLabel")
        title_lbl.setStyleSheet("color:#ddd; font-weight:500;")
        layout.addWidget(title_lbl)
        layout.addStretch(1)
        # Minimize & Close buttons
        btn_style = (
            "QPushButton { border:none; background:#333; color:#ccc; padding:4px 10px; border-radius:4px; }"
            "QPushButton:hover { background:#3f3f3f; color:#fff; }"
            "QPushButton:pressed { background:#222; }"
        )
        min_btn = QPushButton("–")
        min_btn.setFixedWidth(28)
        min_btn.setStyleSheet(btn_style)
        min_btn.clicked.connect(self.showMinimized)
        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(28)
        close_btn.setStyleSheet(btn_style + "QPushButton { color:#e66; } QPushButton:hover { background:#a33; color:#fff; }")
        close_btn.clicked.connect(self.close)
        layout.addWidget(min_btn)
        layout.addWidget(close_btn)
        bar.setLayout(layout)
        # Enable dragging by installing mouse events
        bar.mousePressEvent = self._title_mouse_press  # type: ignore
        bar.mouseMoveEvent = self._title_mouse_move    # type: ignore
        bar.mouseReleaseEvent = self._title_mouse_release  # type: ignore
        return bar

    def _title_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _title_mouse_move(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _title_mouse_release(self, event):
        self._drag_pos = None
        event.accept()
    def get_pad_mapping(self, pad):
        layer = self.layers.get(self.active_layer, {})
        pads = layer.get("pads", {})
        return pads.get(f"{pad[0]},{pad[1]}", None)

    def validate_pad_mapping(self, pad, new_map):
        pads = self.layers.get(self.active_layer, {}).get("pads", {})
        if f"{pad[0]},{pad[1]}" in pads and pads[f"{pad[0]},{pad[1]}"] != new_map:
            QMessageBox.warning(self, "Validation", f"Pad ({pad[0]},{pad[1]}) already has a different mapping.")
            return False
        return True

    def export_config(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Export Config", str(CONFIG_PATH), "YAML Files (*.yaml);;JSON Files (*.json)")
        if fname:
            if fname.endswith(".json"):
                import json
                with open(fname, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2)
            else:
                with open(fname, "w", encoding="utf-8") as f:
                    yaml.dump(self.config, f)
            QMessageBox.information(self, "Exported", f"Config exported to {fname}")

    def import_config(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Import Config", str(CONFIG_PATH), "YAML Files (*.yaml);;JSON Files (*.json)")
        if fname:
            if fname.endswith(".json"):
                import json
                with open(fname, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            else:
                with open(fname, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
            self.active_layer = self.config.get("active_layer", "main")
            self.layers = self.config.get("layers", {})
            self.layer_list.clear()
            self.layer_list.addItems(list(self.layers.keys()))
            self.update_grid()

    def show_help(self):
        help_text = (
            "<b>Launchpad Mapper Help</b><br><br>"
            "<b>Grid:</b> Click a pad to assign/change its action.<br>"
            "<b>Layers:</b> Select or add layers in the sidebar.<br>"
            "<b>Save/Load:</b> Save/load config to YAML.<br>"
            "<b>Export/Import:</b> Export config to YAML/JSON.<br>"
            "<b>Connect:</b> Connect to Launchpad for live preview.<br>"
            "<b>Validation:</b> Duplicate/conflicting actions are prevented.<br>"
            "<b>Color Only:</b> Choose 'color only' or pick a color with no action to just light a pad.<br>"
            "<b>Presets:</b> Use preset controls to load / save mapping sets.<br>"
            "<b>Pad Animation:</b> Physical pad presses render a thick border in the UI.<br>"
        )
        QMessageBox.information(self, "Help/About", help_text)

    def init_ui(self):
        # Central & tab widget with custom title bar wrapper
        central = QWidget()
        central.setObjectName("centralAreaWrapper")
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")

        # --- MAPPINGS TAB CONTENT ---
        mapping_container = QWidget()
        main_layout = QHBoxLayout()

        # --- Grid panel ---
        grid_widget = QWidget()
        grid_widget.setObjectName("gridPanel")
        grid = QGridLayout()
        self.pad_buttons = {}

        # Top control row (y=-1)
        for x in range(8):
            pad = (x, -1)
            mapping = self.get_pad_mapping(pad)
            color = mapping.get("color") if mapping else None
            label = "◀" if x == 2 else ("▶" if x == 3 else "")
            btn = PadButton(x, -1, label=label, color=color, control=True)
            btn.clicked.connect(lambda checked, p=pad: self.edit_pad(p))
            grid.addWidget(btn, 0, x)
            self.pad_buttons[pad] = btn

        # Main 8x8 matrix + side column (x=8)
        for y in range(8):
            side_pad = (8, y)
            side_mapping = self.get_pad_mapping(side_pad)
            side_color = side_mapping.get("color") if side_mapping else None
            side_btn = PadButton(8, y, label="", color=side_color, control=True)
            side_btn.clicked.connect(lambda checked, p=side_pad: self.edit_pad(p))
            grid.addWidget(side_btn, y + 1, 8)
            self.pad_buttons[side_pad] = side_btn
            for x in range(8):
                pad = (x, y)
                mapping = self.get_pad_mapping(pad)
                color = mapping.get("color") if mapping else None
                btn = PadButton(x, y, label="", color=color)
                btn.clicked.connect(lambda checked, p=pad: self.edit_pad(p))
                grid.addWidget(btn, y + 1, x)
                self.pad_buttons[pad] = btn

        grid_widget.setLayout(grid)
        main_layout.addWidget(grid_widget)

        # --- Sidebar ---
        sidebar_widget = QWidget()
        sidebar_widget.setObjectName("sideBar")
        sidebar = QVBoxLayout()

        self.layer_list = QListWidget()
        self.layer_list.addItems(list(self.layers.keys()))
        if self.active_layer in self.layers:
            self.layer_list.setCurrentRow(list(self.layers.keys()).index(self.active_layer))
        self.layer_list.currentTextChanged.connect(self.switch_layer)
        sidebar.addWidget(QLabel("Layers:"))
        sidebar.addWidget(self.layer_list)

        # Layer management
        layer_btn_row = QHBoxLayout()
        self.add_layer_btn = QPushButton("Add Layer")
        self.add_layer_btn.clicked.connect(self.add_layer_dialog)
        self.del_layer_btn = QPushButton("Delete Layer")
        self.del_layer_btn.clicked.connect(self.delete_current_layer)
        layer_btn_row.addWidget(self.add_layer_btn)
        layer_btn_row.addWidget(self.del_layer_btn)
        sidebar.addLayout(layer_btn_row)

        # Presets menu button (replaces separate combo & buttons)
        from PySide6.QtWidgets import QMenu
        self.presets_btn = QPushButton("Layer Presets")
        self.presets_btn.setToolTip("Manage and load presets")
        self.presets_menu = QMenu(self.presets_btn)
        self.presets_btn.setMenu(self.presets_menu)
        self.presets_menu.aboutToShow.connect(self._rebuild_presets_menu)
        sidebar.addWidget(self.presets_btn)

        # Config settings consolidated button
        self.config_btn = QPushButton("Config Settings")
        cfg_menu = QMenu(self.config_btn)
        cfg_menu.addAction("Save Config", self.save_config)
        cfg_menu.addAction("Load Config", self.load_config_dialog)
        cfg_menu.addSeparator()
        cfg_menu.addAction("Export Config", self.export_config)
        cfg_menu.addAction("Import Config", self.import_config)
        self.config_btn.setMenu(cfg_menu)
        sidebar.addWidget(self.config_btn)

        # Help & connect
        self.help_btn = QPushButton("Help/About")
        self.help_btn.clicked.connect(self.show_help)
        sidebar.addWidget(self.help_btn)
        self.connect_btn = QPushButton("Connect Launchpad")
        self.connect_btn.clicked.connect(self.init_midi)
        sidebar.addWidget(self.connect_btn)

        sidebar_widget.setLayout(sidebar)
        main_layout.addWidget(sidebar_widget)
        mapping_container.setLayout(main_layout)

        # --- ANIMATIONS TAB CONTENT ---
        animations_container = self._build_animations_tab()

        self.tabs.addTab(mapping_container, "Mappings")
        self.tabs.addTab(animations_container, "Animations")
        # Root layout containing title bar + main content
        self.title_bar = self._build_title_bar()
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        # Container frame for visual border & shadow separation
        frame = QWidget()
        frame.setObjectName("frameBody")
        frame_layout = QVBoxLayout()
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.addWidget(self.tabs)
        frame.setLayout(frame_layout)
        root_layout.addWidget(self.title_bar)
        root_layout.addWidget(frame, 1)
        central.setLayout(root_layout)
        self.setCentralWidget(central)

        # Shadows for all primary sidebar buttons
        shadow_buttons = [
            self.config_btn, self.presets_btn,
            self.help_btn, self.connect_btn,
            self.add_layer_btn, self.del_layer_btn
        ]
        for b in shadow_buttons:
            eff = QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(18)
            eff.setOffset(0, 3)
            eff.setColor(QColor(0, 0, 0, 160))
            b.setGraphicsEffect(eff)

    # ---------------- Animations Tab ----------------
    def _build_animations_tab(self):
        container = QWidget()
        layout = QHBoxLayout()

        # Left: animation list
        left = QVBoxLayout()
        left.addWidget(QLabel("Animations:"))
        self.animation_list = QListWidget()
        self.animation_list.addItems(sorted(self.animations.keys()))
        self.animation_list.currentTextChanged.connect(self._on_animation_selected)
        left.addWidget(self.animation_list)
        row = QHBoxLayout()
        self.add_animation_btn = QPushButton("New")
        self.add_animation_btn.clicked.connect(self._add_animation)
        self.rename_animation_btn = QPushButton("Rename")
        self.rename_animation_btn.clicked.connect(self._rename_animation)
        self.del_animation_btn = QPushButton("Delete")
        self.del_animation_btn.clicked.connect(self._delete_animation)
        row.addWidget(self.add_animation_btn)
        row.addWidget(self.rename_animation_btn)
        row.addWidget(self.del_animation_btn)
        left.addLayout(row)
        layout.addLayout(left)

        # Middle: frames
        mid = QVBoxLayout()
        mid.addWidget(QLabel("Frames:"))
        self.frame_list = QListWidget()
        self.frame_list.currentRowChanged.connect(self._on_frame_selected)
        mid.addWidget(self.frame_list)
        fr_btns1 = QHBoxLayout()
        self.add_frame_btn = QPushButton("Add")
        self.add_frame_btn.clicked.connect(self._add_frame)
        self.del_frame_btn = QPushButton("Del")
        self.del_frame_btn.clicked.connect(self._delete_frame)
        fr_btns1.addWidget(self.add_frame_btn)
        fr_btns1.addWidget(self.del_frame_btn)
        mid.addLayout(fr_btns1)
        fr_btns2 = QHBoxLayout()
        self.dup_frame_btn = QPushButton("Duplicate")
        self.dup_frame_btn.clicked.connect(self._duplicate_frame)
        self.up_frame_btn = QPushButton("Up")
        self.up_frame_btn.clicked.connect(self._move_frame_up)
        self.down_frame_btn = QPushButton("Down")
        self.down_frame_btn.clicked.connect(self._move_frame_down)
        fr_btns2.addWidget(self.dup_frame_btn)
        fr_btns2.addWidget(self.up_frame_btn)
        fr_btns2.addWidget(self.down_frame_btn)
        mid.addLayout(fr_btns2)
        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("Duration (ms):"))
        self.frame_duration_spin = QSpinBox()
        self.frame_duration_spin.setRange(10, 10000)
        self.frame_duration_spin.setValue(300)
        self.frame_duration_spin.valueChanged.connect(self._update_frame_duration)
        dur_row.addWidget(self.frame_duration_spin)
        mid.addLayout(dur_row)
        layout.addLayout(mid)

        # Right: grid editor (top row, 8x8 matrix, side column)
        right = QVBoxLayout()
        self.anim_grid_widget = QWidget()
        g = QGridLayout()
        self.anim_pad_buttons = {}
        # Top control row y=-1 at row 0
        for x in range(8):
            tbtn = PadButton(x, -1, label="", color=None, control=True)
            tbtn.clicked.connect(lambda checked, p=(x, -1): self._edit_animation_pad(p))
            g.addWidget(tbtn, 0, x)
            self.anim_pad_buttons[(x, -1)] = tbtn
        # Matrix rows + side column
        for y in range(8):
            # side column x=8 at column 8, row offset +1
            sbtn = PadButton(8, y, label="", color=None, control=True)
            sbtn.clicked.connect(lambda checked, p=(8, y): self._edit_animation_pad(p))
            g.addWidget(sbtn, y + 1, 8)
            self.anim_pad_buttons[(8, y)] = sbtn
            for x in range(8):
                btn = PadButton(x, y, label="", color=None)
                btn.clicked.connect(lambda checked, p=(x, y): self._edit_animation_pad(p))
                g.addWidget(btn, y + 1, x)
                self.anim_pad_buttons[(x, y)] = btn
        self.anim_grid_widget.setLayout(g)
        right.addWidget(self.anim_grid_widget)
        play_row = QHBoxLayout()
        self.play_anim_btn = QPushButton("Play")
        self.play_anim_btn.clicked.connect(self._play_selected_animation)
        self.stop_anim_btn = QPushButton("Stop")
        self.stop_anim_btn.clicked.connect(self._stop_animation_playback)
        play_row.addWidget(self.play_anim_btn)
        play_row.addWidget(self.stop_anim_btn)
        right.addLayout(play_row)
        # Export/Import animations
        io_row = QHBoxLayout()
        self.export_anim_btn = QPushButton("Export Animations")
        self.export_anim_btn.clicked.connect(self._export_animations)
        self.import_anim_btn = QPushButton("Import Animations")
        self.import_anim_btn.clicked.connect(self._import_animations)
        io_row.addWidget(self.export_anim_btn)
        io_row.addWidget(self.import_anim_btn)
        right.addLayout(io_row)
        layout.addLayout(right)

        container.setLayout(layout)
        return container

    # -------- Animation Logic Methods ---------
    def _on_animation_selected(self, name):
        self.current_animation = name or None
        self._refresh_frames_list()
        self._refresh_animation_grid()

    def _add_animation(self):
        dlg = TextInputPopup("New Animation", "Animation name:", parent=self)
        name, ok = dlg.get_text()
        if not ok or not name.strip():
            return
        n = name.strip()
        if n in self.animations:
            QMessageBox.warning(self, "Animation", "Name exists.")
            return
        self.animations[n] = {"frames": []}
        self.animation_list.addItem(n)
        self.animation_list.setCurrentRow(self.animation_list.count()-1)
        self.mark_dirty()

    def _rename_animation(self):
        if not self.current_animation:
            return
        old = self.current_animation
        dlg = TextInputPopup("Rename Animation", "New name:", default=old, parent=self)
        new_name, ok = dlg.get_text()
        if not ok:
            return
        new_name = (new_name or "").strip()
        if not new_name:
            return
        if new_name == old:
            return
        if new_name in self.animations:
            QMessageBox.warning(self, "Animation", "Name already exists.")
            return
        # Move frames data
        data = self.animations.pop(old)
        self.animations[new_name] = data
        # Update any pad mappings referencing the old name
        for layer_data in self.layers.values():
            pads = layer_data.get("pads", {}) if isinstance(layer_data, dict) else {}
            for pad_key, mapping in pads.items():
                if isinstance(mapping, dict) and mapping.get("type") == "animation" and mapping.get("name") == old:
                    mapping["name"] = new_name
        # Refresh list
        self.animation_list.clear()
        self.animation_list.addItems(sorted(self.animations.keys()))
        # Set current selection to renamed item
        items = [self.animation_list.item(i).text() for i in range(self.animation_list.count())]
        if new_name in items:
            idx = items.index(new_name)
            self.animation_list.setCurrentRow(idx)
        self.current_animation = new_name
        self.mark_dirty()

    def _delete_animation(self):
        if not self.current_animation:
            return
        reply = QMessageBox.question(self, "Delete Animation", f"Delete '{self.current_animation}'?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.animations.pop(self.current_animation, None)
        self.animation_list.clear()
        self.animation_list.addItems(sorted(self.animations.keys()))
        self.current_animation = None
        self.frame_list.clear()
        self._refresh_animation_grid()
        self.mark_dirty()

    def _refresh_frames_list(self):
        self.frame_list.clear()
        if not self.current_animation:
            return
        frames = self.animations[self.current_animation]["frames"]
        for i, fr in enumerate(frames):
            self.frame_list.addItem(f"Frame {i+1} ({fr.get('duration',300)}ms)")
        if frames:
            self.frame_list.setCurrentRow(0)

    def _add_frame(self):
        if not self.current_animation:
            return
        frames = self.animations[self.current_animation]["frames"]
        frames.append({"duration": self.frame_duration_spin.value(), "pads": {}})
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(self.frame_list.count()-1)
        self.mark_dirty()

    def _delete_frame(self):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        if idx < 0:
            return
        frames = self.animations[self.current_animation]["frames"]
        if idx < len(frames):
            frames.pop(idx)
        self._refresh_frames_list()
        self._refresh_animation_grid()
        self.mark_dirty()

    def _duplicate_frame(self):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        if idx < 0:
            return
        frames = self.animations[self.current_animation]["frames"]
        if idx >= len(frames):
            return
        import copy
        frames.insert(idx+1, copy.deepcopy(frames[idx]))
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(idx+1)
        self.mark_dirty()

    def _move_frame_up(self):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        if idx <= 0:
            return
        frames = self.animations[self.current_animation]["frames"]
        frames[idx-1], frames[idx] = frames[idx], frames[idx-1]
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(idx-1)
        self.mark_dirty()

    def _move_frame_down(self):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        frames = self.animations[self.current_animation]["frames"]
        if idx < 0 or idx >= len(frames)-1:
            return
        frames[idx+1], frames[idx] = frames[idx], frames[idx+1]
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(idx+1)
        self.mark_dirty()

    def _on_frame_selected(self, row):
        self._refresh_animation_grid()
        if self.current_animation and row >= 0:
            frames = self.animations[self.current_animation]["frames"]
            if row < len(frames):
                self.frame_duration_spin.setValue(frames[row].get("duration",300))

    def _refresh_animation_grid(self):
        # Clear all
        if hasattr(self, 'anim_pad_buttons'):
            for btn in self.anim_pad_buttons.values():
                btn.update_color(None)
        if not self.current_animation:
            return
        frames = self.animations[self.current_animation]["frames"]
        idx = self.frame_list.currentRow()
        if idx < 0 or idx >= len(frames):
            return
        pads = frames[idx].get("pads", {})
        for key, col in pads.items():
            try:
                x, y = map(int, key.split(","))
                btn = self.anim_pad_buttons.get((x, y))
                if btn:
                    btn.update_color(col)
            except Exception:
                pass

    def _edit_animation_pad(self, pad):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        if idx < 0:
            return
        frames = self.animations[self.current_animation]["frames"]
        if idx >= len(frames):
            return
        frame = frames[idx]
        color = QColorDialog.getColor()
        if color.isValid():
            frame.setdefault("pads", {})[f"{pad[0]},{pad[1]}"] = color.name()
        else:
            frame.setdefault("pads", {}).pop(f"{pad[0]},{pad[1]}", None)
        self._refresh_animation_grid()
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(idx)
        self.mark_dirty()

    def _update_frame_duration(self, val):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        if idx < 0:
            return
        frames = self.animations[self.current_animation]["frames"]
        if idx < len(frames):
            frames[idx]["duration"] = val
            self._refresh_frames_list()
            self.frame_list.setCurrentRow(idx)
            self.mark_dirty()

    def _play_selected_animation(self):
        if not self.current_animation:
            return
        frames = self.animations[self.current_animation]["frames"]
        if not frames:
            return
        # Preview playback (non-loop; single run) on GUI thread
        self.start_animation_requested.emit(self.current_animation, 1, True)

    def _play_animation_frame(self):
        # Deprecated in favor of scheduler
        pass

    def _start_animation_playback(self, name, iterations: int, preview: bool=False):
        if name not in self.animations:
            return
        self._schedule_animation_run(name, iterations, preview)

    def _stop_animation_playback(self):
        # Stop all animation runs and restore base lights on affected pads
        self._anim_runs = []
        if self.lp:
            try:
                for pad in list(self._anim_composite_prev.keys()):
                    self.lp.set_pad_color(pad, self._get_base_color(pad))
            except Exception:
                pass
        self._anim_composite_prev = {}
        self._anim_timer_active = False
        self.sync_pad_lights()

    def _schedule_animation_run(self, name: str, iterations: int, preview: bool=False):
        frames = self.animations.get(name, {}).get('frames', [])
        if not frames:
            return
        iters = None if iterations <= 0 else int(iterations)
        self._anim_start_seq += 1
        run = {
            'name': name,
            'frames': frames,
            'index': 0,
            'time_left': int(frames[0].get('duration', 300)),
            'iterations': iters,
            'started_seq': self._anim_start_seq,
        }
        self._anim_runs.append(run)
        if not self._anim_timer_active:
            self._anim_timer_active = True
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._anim_scheduler_tick)

    def _anim_scheduler_tick(self):
        # If no runs, restore any composite leftovers and stop timer
        if not self._anim_runs:
            if self.lp:
                try:
                    for pad in list(self._anim_composite_prev.keys()):
                        self.lp.set_pad_color(pad, self._get_base_color(pad))
                except Exception:
                    pass
            self._anim_composite_prev = {}
            self._anim_timer_active = False
            self.sync_pad_lights()
            return
        # Compose current frame outputs; later runs override earlier on conflicts
        composite = {}
        for run in sorted(self._anim_runs, key=lambda r: r['started_seq']):
            fr = run['frames'][run['index']]
            for key, col in fr.get('pads', {}).items():
                try:
                    x, y = map(int, key.split(','))
                    r = int(col[1:3],16); g=int(col[3:5],16); b=int(col[5:7],16)
                    composite[(x,y)] = (r,g,b)
                except Exception:
                    pass
        # Apply diffs vs previous composite
        prev = self._anim_composite_prev or {}
        to_set = {pad: rgb for pad, rgb in composite.items() if pad not in prev or prev.get(pad) != rgb}
        to_restore = [pad for pad in prev.keys() if pad not in composite]
        if self.lp:
            try:
                for pad, rgb in to_set.items():
                    self.lp.set_pad_color(pad, rgb)
                for pad in to_restore:
                    self.lp.set_pad_color(pad, self._get_base_color(pad))
            except Exception:
                pass
        self._anim_composite_prev = composite
        # Determine dt (ms) until next change across runs
        dt = min(max(1, int(r['time_left'])) for r in self._anim_runs)
        # Advance time and progress frames, removing finished runs
        remaining = []
        for run in self._anim_runs:
            run['time_left'] = int(run['time_left']) - dt
            while run['time_left'] <= 0:
                run['index'] += 1
                frames = run['frames']
                if run['index'] >= len(frames):
                    if run['iterations'] is None:
                        run['index'] = 0
                    elif run['iterations'] > 1:
                        run['iterations'] -= 1
                        run['index'] = 0
                    else:
                        # finished
                        break
                run['time_left'] += int(frames[run['index']].get('duration', 300))
            # Keep run if not finished
            if run['index'] < len(run['frames']) or (run['iterations'] is None):
                remaining.append(run)
        self._anim_runs = remaining
        # Schedule next tick
        from PySide6.QtCore import QTimer
        if self._anim_runs:
            QTimer.singleShot(dt, self._anim_scheduler_tick)
        else:
            QTimer.singleShot(0, self._anim_scheduler_tick)

    def _get_base_color(self, pad):
        """Return the base RGB color for a pad from the current layer mapping, or off if none."""
        mapping = self.layers.get(self.active_layer, {}).get("pads", {}).get(f"{pad[0]},{pad[1]}")
        if isinstance(mapping, dict):
            col = mapping.get("color")
            if isinstance(col, str) and len(col) == 7 and col.startswith('#'):
                try:
                    r = int(col[1:3],16); g = int(col[3:5],16); b = int(col[5:7],16)
                    return (r, g, b)
                except Exception:
                    pass
        return (0,0,0)

    def _start_animation_from_signal(self, name: str, iterations: int, preview: bool):
        # Wrapper to ensure playback starts on the GUI thread
        self._start_animation_playback(name, iterations, preview)

    def _compute_base_colors(self):
        base = {}
        pads = self.layers.get(self.active_layer, {}).get("pads", {})
        for key, mapping in pads.items():
            col = mapping.get("color") if isinstance(mapping, dict) else None
            if isinstance(col, str) and len(col) == 7 and col.startswith('#'):
                try:
                    r = int(col[1:3], 16); g = int(col[3:5], 16); b = int(col[5:7], 16)
                    x, y = map(int, key.split(","))
                    # Skip pads that are under animation control for this playback
                    if hasattr(self, "_animation_scope") and self._animation_scope and (x, y) in self._animation_scope:
                        continue
                    base[(x, y)] = (r, g, b)
                except Exception:
                    pass
        return base

    def _apply_base_colors_to_hardware(self):
        if not self.lp:
            return
        try:
            # Clear everything first
            self.lp.clear()
            for x in range(8):
                self.lp.set_pad_color((x, -1), (0,0,0))
            for y in range(8):
                self.lp.set_pad_color((8, y), (0,0,0))
            # Apply base
            for (x, y), rgb in (self._animation_base_colors or {}).items():
                self.lp.set_pad_color((x, y), rgb)
        except Exception:
            pass

    def _export_animations(self):
        fname, _ = QFileDialog.getSaveFileName(self, "Export Animations", str(PROJECT_ROOT / "animations.yaml"), "YAML Files (*.yaml)")
        if not fname:
            return
        try:
            with open(fname, "w", encoding="utf-8") as f:
                yaml.dump(self.animations, f)
            QMessageBox.information(self, "Animations", f"Exported animations to {fname}")
        except Exception as e:
            QMessageBox.warning(self, "Animations", f"Failed to export: {e}")

    def _import_animations(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Import Animations", str(PROJECT_ROOT), "YAML Files (*.yaml)")
        if not fname:
            return
        try:
            with open(fname, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError("Invalid animations file format")
            # Merge or overwrite? We'll merge keys and overwrite conflicts
            self.animations.update(data)
            # Refresh animations list in UI
            self.animation_list.clear()
            self.animation_list.addItems(sorted(self.animations.keys()))
            QMessageBox.information(self, "Animations", f"Imported animations from {fname}")
        except Exception as e:
            QMessageBox.warning(self, "Animations", f"Failed to import: {e}")

    def apply_theme(self):
        """Apply the 'Minimalistic Black' theme globally so popups match the main window."""
        app = QApplication.instance()
        if app:
            # Wrap Fusion style with custom caret arrow style
            base = QStyleFactory.create("Fusion") if hasattr(QStyleFactory, 'create') else app.style()
            try:
                app.setStyle(base)
            except Exception:
                app.setStyle("Fusion")
            palette = QPalette()
            palette.setColor(QPalette.Window, QColor(30,30,32))
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(24,24,26))
            palette.setColor(QPalette.AlternateBase, QColor(40,40,44))
            palette.setColor(QPalette.ToolTipBase, Qt.white)
            palette.setColor(QPalette.ToolTipText, Qt.white)
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(45,45,50))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.Highlight, QColor(70,120,200))
            palette.setColor(QPalette.HighlightedText, Qt.white)
            app.setPalette(palette)
            # Install custom arrow style
            try:
                app.setStyle(CaretSpinStyle(app.style()))
            except Exception:
                pass
            if self.custom_font_family:
                app.setFont(QFont(self.custom_font_family, 10))
            else:
                app.setFont(QFont("Segoe UI", 9))
            # Global stylesheet so dialogs inherit styling
            app.setStyleSheet(
                """
                /* Main window surfaces now square (no rounded corners) */
                #centralAreaWrapper { background: #101011; }
                #frameBody { background:#1e1e20; border:1px solid #2c2c2f; }
                #mainTitleBar { background:#141416; }
                #mainTitleBar QLabel#titleLabel { padding-left:4px; }
                #gridPanel { background: #252528; border:1px solid #333; padding:8px; }
                #sideBar { background:#202022; border:1px solid #333; padding:8px; }
                /* Popup windows keep rounded corners */
                #titleBar { background:#141416; border-top-left-radius:10px; border-top-right-radius:10px; }
                #popupBody { background:#1e1e20; border:1px solid #2c2c2f; border-bottom-left-radius:10px; border-bottom-right-radius:10px; }
                /* Color picker embedded dialog */
                QWidget#colorPickerHost { background:#1e1e20; }
                QColorDialog, QColorDialog QWidget { background:#1e1e20; }
                QDialog { background: transparent; }
                QListWidget { background:#18181a; border:1px solid #333; border-radius:6px; }
                QListWidget::item:selected { background:#3a3f55; }
                QPushButton { background:#2d2f35; border:1px solid #444; border-radius:6px; padding:4px 10px; color:#eee; }
                QPushButton:hover { background:#3a3d45; }
                QPushButton:pressed { background:#1f2126; }
                QLineEdit, QComboBox, QSpinBox { background:#18181a; color:#eee; border:1px solid #555; border-radius:4px; padding:3px 6px; }
                /* Transparent knappar – vi ritar pilar själva i CaretSpinStyle */
                QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button { background:transparent; border:none; width:18px; margin:0; padding:0; }
                QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background:rgba(255,255,255,0.10); }
                QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width:22px; border-left:1px solid #444; background:transparent; }
                QComboBox::drop-down:hover { background:rgba(255,255,255,0.10); }
                QSpinBox::up-arrow, QSpinBox::down-arrow, QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow, QComboBox::down-arrow { image:none; }
                /* Force no text underline anywhere */
                QPushButton { text-decoration: none; }
                QToolTip { color:#fff; background:#444; border:1px solid #666; }
                * { font-family: "%s"; }
                """ % (self.custom_font_family or "Segoe UI")
            )

    def add_layer_dialog(self):
        name, ok = QInputDialog.getText(self, "Add Layer", "Layer name:")
        if not ok or not name.strip():
            return
        layer_name = name.strip()
        if layer_name in self.layers:
            QMessageBox.warning(self, "Layer", "Layer already exists.")
            return
        # Initialize with default navigation buttons (same colors as main layer spec)
        self.layers[layer_name] = {"pads": {
            "2,-1": {"type": "layer nav", "nav": "prev", "color": "#2980b9"},
            "3,-1": {"type": "layer nav", "nav": "next", "color": "#8e44ad"}
        }}
        self.layer_list.addItem(layer_name)
        row = self.layer_list.count() - 1
        self.layer_list.setCurrentRow(row)
        self.active_layer = layer_name
        self.update_grid()

    def delete_current_layer(self):
        current = self.active_layer
        if current == "main":
            QMessageBox.warning(self, "Layer", "Cannot delete the primary 'main' layer.")
            return
        reply = QMessageBox.question(self, "Delete Layer", f"Delete layer '{current}'?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.layers.pop(current, None)
        # Update list
        self.layer_list.clear()
        self.layer_list.addItems(list(self.layers.keys()))
        # Fallback to main
        self.active_layer = "main" if "main" in self.layers else (next(iter(self.layers.keys())) if self.layers else "main")
        if self.active_layer not in self.layers:
            self.layers[self.active_layer] = {"pads": {}}
            self.layer_list.addItem(self.active_layer)
        # Select row
        names = list(self.layers.keys())
        idx = names.index(self.active_layer)
        self.layer_list.setCurrentRow(idx)
        self.update_grid()

    def load_custom_font(self):
        """Attempt to load Cascadia Code font from several possible locations.

        In release mode this is silent; in debug mode we log the first
        successful path or a fallback message.
        """
        candidates: list[Path] = []
        candidates.append(PROJECT_ROOT / "fonts" / "CASCADIACODE.TTF")
        try:
            candidates.append(Path(__file__).resolve().parent / "fonts" / "CASCADIACODE.TTF")
        except Exception:
            pass
        try:
            candidates.append(Path(sys.executable).resolve().parent / "fonts" / "CASCADIACODE.TTF")
        except Exception:
            pass
        try:
            candidates.append(Path(sys.executable).resolve().parent / "_internal" / "fonts" / "CASCADIACODE.TTF")
        except Exception:
            pass
        for fp in candidates:
            try:
                if fp.exists():
                    fid = QFontDatabase.addApplicationFont(str(fp))
                    if fid != -1:
                        fams = QFontDatabase.applicationFontFamilies(fid)
                        if fams:
                            self.custom_font_family = fams[0]
                            _debug_log(f"Loaded custom font '{self.custom_font_family}' from: {fp}")
                            return
            except Exception:
                continue
        _debug_log("Custom font CASCADIACODE.TTF not found; using fallback.")
    def init_midi(self, auto: bool=False):
        if not LaunchpadController:
            if not auto:
                QMessageBox.warning(
                    self,
                    "MIDI",
                    "Launchpad backend not available. Install dependencies:\npip install mido python-rtmidi"
                )
            return
        if self.lp:
            try:
                self.lp.close()
            except Exception:
                pass
        self.lp = LaunchpadController()
        try:
            self.lp.open()
        except Exception as e:
            if auto:
                # Small informational dialog only if auto attempt fails; user can still click Connect later
                QMessageBox.information(self, "Launchpad", f"Ingen Launchpad hittades (autokonnect misslyckades).\nDu kan koppla in den och klicka 'Connect Launchpad'.\n\nFel: {e}")
            else:
                QMessageBox.warning(self, "MIDI", f"Could not open Launchpad: {e}")
            self.lp = None
            return
        # Register callbacks
        self.lp.on_press = self.on_pad_press
        self.lp.on_release = self.on_pad_release
        self.statusBar().showMessage(
            "Launchpad connected (virtual mode)" if self.lp.virtual else "Launchpad connected", 5000
        )
        self.sync_pad_lights()

    # --- Config / persistence helpers ---
    def _load_config_on_startup(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    self.config = data
                    self.active_layer = self.config.get("active_layer", "main")
                    self.layers = self.config.get("layers", {}) or {"main": {"pads": {}}}
                    self.animations = self.config.get("animations", {}) or {}
            except Exception:
                pass

    def _normalize_all_mappings(self):
        def legacy_to_space(t: str) -> str:
            return {"layer_nav": "layer nav", "switch_layer": "switch layer", "run_process": "run process"}.get(t, t)
        changed = False
        for layer_data in self.layers.values():
            pads = layer_data.get("pads", {}) if isinstance(layer_data, dict) else {}
            for pad_key, mapping in list(pads.items()):
                if not isinstance(mapping, dict):
                    continue
                t = mapping.get("type")
                if isinstance(t, str):
                    t2 = legacy_to_space(t)
                    if t2 == "color only":
                        t2 = "color"
                    if t2 != t:
                        mapping["type"] = t2
                        changed = True
        # Don't mark dirty during early initialization
        if changed and getattr(self, "_ready", False):
            self.mark_dirty()

    def mark_dirty(self):
        self._dirty = True
        if self._save_debounce_timer is None:
            self._save_debounce_timer = QTimer(self)
            self._save_debounce_timer.setSingleShot(True)
            self._save_debounce_timer.timeout.connect(lambda: self.save_config(silent=True))
        self._save_debounce_timer.start(800)

    def closeEvent(self, event):
        # Persist UI visibility (hidden vs shown) so next start matches user's last state
        try:
            ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
            ui['start_hidden'] = bool(self.isHidden())
            if isinstance(self.config, dict):
                self.config['ui'] = ui
        except Exception:
            pass
        # Silent autosave of current config (includes animations) before closing
        try:
            self.save_config(silent=True)
        except Exception:
            pass
        # Ensure hardware LEDs are cleared when window closes
        try:
            if self.lp:
                self.lp.clear(full=True)
                self.lp.close()
        except Exception:
            pass
        super().closeEvent(event)

    # --- System tray ---
    def _install_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self)
        # Försök använda app.ico om packat
        icon_path = None
        for cand in [Path(__file__).parent.parent / 'icons' / 'app.ico', Path('icons/app.ico')]:
            if cand.exists():
                icon_path = cand
                break
        if icon_path:
            self.tray.setIcon(QIcon(str(icon_path)))
        else:
            self.tray.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        menu = QMenu()
        show_act = QAction("Open Window", self)
        show_act.triggered.connect(self._tray_show)
        menu.addAction(show_act)
        reload_lp = QAction("Reconnect Launchpad", self)
        reload_lp.triggered.connect(lambda: self.init_midi(auto=False))
        menu.addAction(reload_lp)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self._tray_quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _tray_show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_quit(self):
        self.close()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isHidden():
                self._tray_show()
            else:
                self.hide()

    # --- Presets management ---
    def ensure_presets_dir(self):
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        # One-time copy of packaged default presets into AppData if missing.
        # Use a sentinel file to avoid re-populating after user deletes defaults.
        marker = PRESETS_DIR / '.migrated'
        if not marker.exists():
            try:
                src_dirs: list[Path] = []
                # Dev tree
                src_dirs.append(PROJECT_ROOT / 'presets')
                # Beside this module
                try:
                    src_dirs.append(Path(__file__).resolve().parent.parent / 'presets')
                except Exception:
                    pass
                # Frozen onedir: alongside executable or under _internal
                try:
                    exe_dir = Path(sys.executable).resolve().parent
                    src_dirs.append(exe_dir / 'presets')
                    src_dirs.append(exe_dir / '_internal' / 'presets')
                except Exception:
                    pass
                # Copy any *.yaml that don't already exist in AppData presets
                for d in src_dirs:
                    if not d or not d.exists():
                        continue
                    for yml in d.glob('*.yaml'):
                        dest = PRESETS_DIR / yml.name
                        if not dest.exists():
                            try:
                                shutil.copy2(str(yml), str(dest))
                            except Exception:
                                pass
                # Write sentinel
                try:
                    marker.write_text('migrated', encoding='utf-8')
                except Exception:
                    pass
            except Exception:
                pass

    def create_initial_preset_if_missing(self):
        empty = PRESETS_DIR / "empty.yaml"
        if not empty.exists():
            sample = {
                "layer": "main",
                "pads": {
                    "2,-1": {"type": "layer nav", "nav": "prev", "color": "#2980b9"},
                    "3,-1": {"type": "layer nav", "nav": "next", "color": "#8e44ad"}
                },
                "animations": {}
            }
            with open(empty, "w", encoding="utf-8") as f:
                yaml.dump(sample, f)

    # ---- Presets System (menu-based) ----
    def _rebuild_presets_menu(self):
        self.presets_menu.clear()
        presets = sorted(PRESETS_DIR.glob("*.yaml"))
        if presets:
            for p in presets:
                name = p.stem
                act = self.presets_menu.addAction(name, lambda n=name: self.load_preset(n))
                if name == self.current_preset_name:
                    font = act.font()
                    font.setBold(True)
                    act.setFont(font)
        else:
            a = self.presets_menu.addAction("(No presets)")
            a.setEnabled(False)
        self.presets_menu.addSeparator()
        self.presets_menu.addAction("+ New Layer Preset", self.new_preset_dialog)
        self.presets_menu.addAction("Save Layer Preset", self.save_current_preset)
        self.presets_menu.addAction("Save Layer Preset As...", self.save_current_preset_as)
        self.presets_menu.addAction("Delete Layer Preset", self.delete_current_preset)
        self.presets_menu.addSeparator()
        self.presets_menu.addAction("Import Preset From File...", self.import_preset_file)

    def new_preset_dialog(self):
        name, ok = QInputDialog.getText(self, "New Preset", "Preset name:")
        if not ok or not name.strip():
            return
        safe = self._sanitize_name(name)
        path = PRESETS_DIR / f"{safe}.yaml"
        if path.exists():
            QMessageBox.warning(self, "Preset", "Preset already exists.")
            return
        # Start from blank current layer (preserving other layers)
        if self.active_layer not in self.layers:
            self.layers[self.active_layer] = {"pads": {}}
        # Keep navigation pads if already present, otherwise seed them
        pads = self.layers[self.active_layer].get("pads", {})
        for nav_def in (("2,-1", {"type": "layer nav", "nav": "prev", "color": "#2980b9"}),
                        ("3,-1", {"type": "layer nav", "nav": "next", "color": "#8e44ad"})):
            pads.setdefault(nav_def[0], nav_def[1])
        # Clear all other pads for this layer only
        for k in list(pads.keys()):
            if k not in ("2,-1","3,-1"):
                pads.pop(k)
        self.layers[self.active_layer]["pads"] = pads
        self.update_grid()
        self.current_preset_name = safe
        self._write_preset_file(path)
        self.statusBar().showMessage(f"Created preset '{safe}'", 4000)

    def _sanitize_name(self, name: str) -> str:
        return "".join(c for c in name if c.isalnum() or c in ('-','_')).strip()

    def _write_preset_file(self, path: Path):
        # Layer-scoped preset file; store pads for the active layer and embed referenced animations
        pads = self.layers.get(self.active_layer, {}).get("pads", {})
        referenced = set()
        for m in pads.values():
            if isinstance(m, dict) and m.get("type") == "animation":
                name = m.get("name")
                if name:
                    referenced.add(name)
        anims = {k: v for k, v in self.animations.items() if k in referenced}
        data = {
            "layer": self.active_layer,
            "pads": pads,
            "animations": anims,
        }
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def save_current_preset(self):
        if not self.current_preset_name:
            self.save_current_preset_as()
            return
        path = PRESETS_DIR / f"{self.current_preset_name}.yaml"
        self._write_preset_file(path)
        self.statusBar().showMessage(f"Saved layer preset '{self.current_preset_name}'", 4000)

    def save_current_preset_as(self):
        name, ok = QInputDialog.getText(self, "Save Preset As", "Preset name:")
        if not ok or not name.strip():
            return
        safe = self._sanitize_name(name)
        if not safe:
            QMessageBox.warning(self, "Preset", "Invalid name.")
            return
        self.current_preset_name = safe
        self.save_current_preset()

    def delete_current_preset(self):
        if not self.current_preset_name:
            QMessageBox.information(self, "Preset", "No preset loaded.")
            return
        # Enforce at least one preset existing
        existing = list(PRESETS_DIR.glob("*.yaml"))
        if len(existing) <= 1:
            QMessageBox.warning(self, "Preset", "Cannot delete the last remaining preset.")
            return
        reply = QMessageBox.question(self, "Delete Layer Preset", f"Delete preset '{self.current_preset_name}'?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        path = PRESETS_DIR / f"{self.current_preset_name}.yaml"
        try:
            if path.exists():
                path.unlink()
        except Exception as e:
            QMessageBox.warning(self, "Preset", f"Could not delete: {e}")
            return
        deleted = self.current_preset_name
        self.current_preset_name = None
        remaining = sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))
        target = None
        if remaining:
            prevs = [n for n in remaining if n < deleted]
            target = prevs[-1] if prevs else remaining[0]
        elif (PRESETS_DIR / "empty.yaml").exists():
            target = "empty"
        self.statusBar().showMessage(f"Layer preset '{deleted}' deleted", 4000)
        if target:
            self.load_preset(target)
        else:
            self._apply_default_config()

    def import_preset_file(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Import Preset", str(PRESETS_DIR), "YAML Files (*.yaml)")
        if not fname:
            return
        src = Path(fname)
        if not src.exists():
            return
        base = src.stem
        safe = self._sanitize_name(base) or "imported_preset"
        dest = PRESETS_DIR / f"{safe}.yaml"
        # Avoid overwrite silently
        i = 1
        while dest.exists():
            dest = PRESETS_DIR / f"{safe}_{i}.yaml"
            i += 1
        try:
            with open(src, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            with open(dest, "w", encoding="utf-8") as f:
                yaml.dump(data, f)
        except Exception as e:
            QMessageBox.warning(self, "Import", f"Failed to import: {e}")
            return
        self.load_preset(dest.stem)
        self.statusBar().showMessage(f"Imported layer preset '{dest.stem}'", 4000)

    def load_preset(self, name: str):
        """Load a layer-scoped preset: replace only current layer's pads."""
        path = PRESETS_DIR / f"{name}.yaml"
        if not path.exists():
            QMessageBox.warning(self, "Preset", "Preset file missing.")
            return
        self._stop_animation_playback()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            QMessageBox.warning(self, "Preset", f"Failed to load: {e}")
            return
        # Backward compatibility: old full-config style
        if "layers" in data:
            active = data.get("active_layer", self.active_layer)
            layers = data.get("layers", {})
            pads = layers.get(active, {}).get("pads", {})
        else:
            pads = data.get("pads", {})
            # Merge embedded animations (if present) into global animations set
            emb = data.get("animations", {})
            if isinstance(emb, dict):
                self.animations.update(emb)
        if self.active_layer not in self.layers:
            self.layers[self.active_layer] = {"pads": {}}
        # Preserve navigation buttons if missing
        nav_prev = self.layers[self.active_layer]["pads"].get("2,-1", {"type": "layer nav", "nav": "prev", "color": "#2980b9"})
        nav_next = self.layers[self.active_layer]["pads"].get("3,-1", {"type": "layer nav", "nav": "next", "color": "#8e44ad"})
        self.layers[self.active_layer]["pads"] = {**pads}
        self.layers[self.active_layer]["pads"].setdefault("2,-1", nav_prev)
        self.layers[self.active_layer]["pads"].setdefault("3,-1", nav_next)
        self.current_preset_name = name
        # Hard clear hardware to avoid lingering lights from previous preset
        if self.lp:
            try:
                self.lp.clear(full=True)
            except Exception:
                pass
        self.update_grid()
        # Extra refresh to avoid first-click blanking
        self.sync_pad_lights()
        self.update_grid()
        self.sync_pad_lights()
        self.statusBar().showMessage(f"Loaded layer preset '{name}'", 4000)

    def _apply_default_config(self):
        self.config = {"active_layer": "main", "layers": {"main": {"pads": {
            "2,-1": {"type": "layer nav", "nav": "prev", "color": "#2980b9"},
            "3,-1": {"type": "layer nav", "nav": "next", "color": "#8e44ad"}
        }}}, "animations": {}}
        self.active_layer = "main"
        self.layers = self.config["layers"]
        self.animations = self.config.get("animations", {})
        self.layer_list.clear()
        self.layer_list.addItems(["main"])
        self.update_grid()
        if self.lp:
            try:
                self.lp.clear(full=True)
            except Exception:
                pass
            self.sync_pad_lights()

    def new_empty_preset(self):
        # Backward compatibility: Use default config logic (does not set current_preset_name)
        self._apply_default_config()
    def save_as_preset(self):
        name, ok = QInputDialog.getText(self, "Preset Name", "Enter preset name:")
        if not ok or not name.strip():
            return
        safe = "".join(c for c in name if c.isalnum() or c in ('-','_')).strip()
        if not safe:
            QMessageBox.warning(self, "Preset", "Invalid preset name.")
            return
        path = PRESETS_DIR / f"{safe}.yaml"
        data = {"active_layer": self.active_layer, "layers": self.layers, "animations": self.animations}
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
        self.refresh_preset_combo()
        QMessageBox.information(self, "Preset", f"Saved preset '{safe}'.")

    # load_selected_preset deprecated (use load_preset via menu)

    # --- Pad press callbacks ---
    def on_pad_press(self, pad):
        x, y = pad
        mapping = self.get_pad_mapping(pad)
        if mapping and mapping.get("type") == "layer nav":
            direction = mapping.get("nav")
            names = list(self.layers.keys())
            if names:
                cur = names.index(self.active_layer) if self.active_layer in names else 0
                if direction == "prev":
                    new_index = (cur - 1) % len(names)
                else:
                    new_index = (cur + 1) % len(names)
                self.active_layer = names[new_index]
                self.layer_list.setCurrentRow(new_index)
                self.update_grid()
        elif mapping and mapping.get("type") == "animation":
            anim_name = mapping.get("name")
            if anim_name and anim_name in self.animations:
                iterations = mapping.get("iterations", 1)
                # Emit to ensure main-thread start (0=infinite, 1=single cycle)
                self.start_animation_requested.emit(anim_name, iterations, False)
        elif mapping and mapping.get("type") == "launch app":
            path = mapping.get("path")
            if path:
                try:
                    self._launch_app_silent(path)
                except Exception as e:
                    QMessageBox.warning(self, "Launch App", f"Failed to launch: {e}")
        elif mapping and mapping.get("type") == "run process":
            cmd = mapping.get("command")
            if cmd:
                try:
                    subprocess.Popen(cmd, shell=True)
                except Exception as e:
                    QMessageBox.warning(self, "Run Process", f"Failed: {e}")
        elif mapping and mapping.get("type") == "hotkey":
            keys = mapping.get("keys")
            if keys:
                self._perform_hotkey(keys)
        elif mapping and mapping.get("type") == "media control":
            act = mapping.get("action")
            steps = int(mapping.get("steps", 1)) if mapping.get("action") else 1
            if act:
                self._perform_media_control(act, steps)
        self.pad_pressed.emit(x, y)
    def on_pad_release(self, pad):
        self.pad_released.emit(pad[0], pad[1])
    def animate_pad_press(self, x, y):
        btn = self.pad_buttons.get((x, y))
        if btn:
            btn.set_pressed(True)
    def animate_pad_release(self, x, y):
        btn = self.pad_buttons.get((x, y))
        if btn:
            btn.set_pressed(False)

    def edit_pad(self, pad):
        mapping = self.get_pad_mapping(pad)
        dlg = PadEditorDialog(pad, mapping, self, animation_names=sorted(self.animations.keys()))
        if dlg.exec() == QDialog.Accepted:
            type_ = dlg.action_type.currentText()
            val = dlg.action_value.text()
            layer = dlg.layer_select.text()
            color = dlg.selected_color
            anim_name = dlg.animation_select.currentText() if type_ == "animation" else None
            pad_key = f"{pad[0]},{pad[1]}"
            # Normalize selected type (legacy underscores -> spaces)
            legacy_map = {"layer_nav": "layer nav", "switch_layer": "switch layer", "run_process": "run process"}
            type_ = legacy_map.get(type_, type_)
            if type_ == "none" and not color:
                # remove mapping
                self.layers[self.active_layer]["pads"].pop(pad_key, None)
            else:
                new_map = {}
                if type_ == "color only" or (type_ == "none" and color):
                    new_map["type"] = "color"
                else:
                    new_map["type"] = type_
                    if type_ == "run process":
                        new_map["command"] = val
                    elif type_ == "launch app":
                        new_map["path"] = dlg.launch_path.text().strip()
                    elif type_ == "hotkey":
                        new_map["keys"] = dlg.hotkey_edit.text().strip()
                    elif type_ == "media control":
                        new_map["action"] = dlg.media_select.currentText()
                        new_map["steps"] = int(getattr(dlg, 'media_steps').value())
                    elif type_ == "switch layer":
                        new_map["layer"] = layer
                    elif type_ == "layer nav":
                        new_map["nav"] = dlg.nav_select.currentText() or "prev"
                    elif type_ == "animation":
                        if not anim_name:
                            QMessageBox.warning(self, "Animation", "Select an animation.")
                            return
                        new_map["name"] = anim_name
                        new_map["iterations"] = dlg.animation_loop_iterations.value()
                if color:
                    new_map["color"] = color
                    self.last_color = color
                # Do not persist a color-only mapping without a color value
                if new_map.get("type") == "color" and not color:
                    self.layers[self.active_layer]["pads"].pop(pad_key, None)
                else:
                    self.layers[self.active_layer]["pads"][pad_key] = new_map
            self.update_grid()
            self.sync_pad_lights()
            if self._ready:
                self.mark_dirty()
    def _launch_app_silent(self, path: str):
        """Startar ett program utan terminalspam / nytt konsolfönster.

        På Windows används STARTF_USESHOWWINDOW + CREATE_NO_WINDOW för att tysta.
        Stdout/stderr dirigeras till devnull. """
        try:
            devnull = subprocess.DEVNULL
        except Exception:
            devnull = open(os.devnull, 'wb')  # nosec
        kwargs = {'stdout': devnull, 'stderr': devnull}
        if sys.platform == 'win32':
            CREATE_NO_WINDOW = 0x08000000
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs['startupinfo'] = si
            kwargs['creationflags'] = CREATE_NO_WINDOW
        try:
            subprocess.Popen(path, **kwargs)
        except Exception as e:
            # Faller tillbaka till vanlig launch om första försöket misslyckas
            try:
                subprocess.Popen(path)
            except Exception:
                raise e
    def update_grid(self):
        for pad, btn in self.pad_buttons.items():
            mapping = self.get_pad_mapping(pad)
            if mapping:
                btn.update_color(mapping.get("color"))
            else:
                btn.update_color(None)
        self.sync_pad_lights()
    def switch_layer(self, name):
        self.active_layer = name
        self.update_grid()
        self.sync_pad_lights()
    def save_config(self, silent: bool=False):
        """Persist full configuration (layers + animations)."""
        self.config["active_layer"] = self.active_layer
        self.config["layers"] = self.layers
        self.config["animations"] = self.animations
        # Ensure UI section exists and update visibility snapshot
        try:
            ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
            ui['start_hidden'] = bool(self.isHidden())
            self.config['ui'] = ui
        except Exception:
            pass
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(self.config, f)
            if not silent:
                QMessageBox.information(self, "Saved", f"Config saved to {CONFIG_PATH}")
        except Exception as e:
            if not silent:
                QMessageBox.warning(self, "Save", f"Failed to save config: {e}")
        self._dirty = False
    def load_config(self):
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {"active_layer": "main", "layers": {"main": {"pads": {}}}}
    def load_config_dialog(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Load Config", str(CONFIG_PATH), "YAML Files (*.yaml)")
        if fname:
            with open(fname, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
            self.active_layer = self.config.get("active_layer", "main")
            self.layers = self.config.get("layers", {})
            self.animations = self.config.get("animations", {})
            self.layer_list.clear()
            self.layer_list.addItems(list(self.layers.keys()))
            self.update_grid()
            self._normalize_all_mappings()
            self.mark_dirty()

    # Duplicate init_ui removed to ensure control buttons (top & side) are displayed.

class CaretSpinStyle(QProxyStyle):
    """Ritar tydliga vita trianglar (upp/ned) + combobox-pil mot transparent bakgrund."""
    def drawPrimitive(self, element, option, painter, widget=None):
        from PySide6.QtGui import QPolygon
        targets = (QStyle.PE_IndicatorSpinUp, QStyle.PE_IndicatorSpinDown, QStyle.PE_IndicatorArrowDown)
        if element in targets:
            painter.save()
            r = option.rect.adjusted(2, 2, -2, -2)
            painter.setRenderHint(QPainter.Antialiasing, True)
            # Svag hover-bakgrund
            if option.state & QStyle.State_MouseOver:
                painter.fillRect(option.rect, QColor(255,255,255,28))
            # Triangelpunkter
            cx = r.center().x(); cy = r.center().y()
            if element == QStyle.PE_IndicatorSpinUp:
                pts = [QPoint(cx, cy-5), QPoint(cx-5, cy+3), QPoint(cx+5, cy+3)]
            elif element == QStyle.PE_IndicatorSpinDown:
                pts = [QPoint(cx, cy+5), QPoint(cx-5, cy-3), QPoint(cx+5, cy-3)]
            else:  # combobox
                pts = [QPoint(cx, cy+5), QPoint(cx-6, cy-2), QPoint(cx+6, cy-2)]
            painter.setBrush(QColor(255,255,255))
            painter.setPen(QPen(QColor(255,255,255), 1))
            painter.drawPolygon(QPolygon(pts))
            painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)

    # (Tidigare monkey patch helpers borttagna – allt ligger nu inne i MainWindow.)

def main():
    # Determine desired start_hidden: CLI flag overrides saved preference
    cli_hidden = ('--background' in sys.argv) or ('--hidden' in sys.argv)
    if '--background' in sys.argv:
        sys.argv.remove('--background')
    if '--hidden' in sys.argv:
        sys.argv.remove('--hidden')
    saved_hidden = False
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, 'r', encoding='utf-8') as _cf:
                _cfg = yaml.safe_load(_cf) or {}
            if isinstance(_cfg, dict):
                ui = _cfg.get('ui') or {}
                if isinstance(ui, dict):
                    saved_hidden = bool(ui.get('start_hidden', False))
    except Exception:
        pass
    start_hidden = cli_hidden or saved_hidden
    # Optional: --preset <name> to force-load a layer preset file (presets/<name>.yaml) after config
    preset_to_load = None
    if '--preset' in sys.argv:
        try:
            idx = sys.argv.index('--preset')
            if idx + 1 < len(sys.argv):
                preset_to_load = sys.argv[idx + 1]
                # remove both
                del sys.argv[idx:idx+2]
            else:
                # remove flag only if no value provided
                sys.argv.remove('--preset')
        except ValueError:
            pass
    try:
        _debug_log('Creating QApplication...')
        app = QApplication(sys.argv)
        _debug_log('QApplication created. Constructing MainWindow...')
        win = MainWindow(start_hidden=start_hidden)
        _debug_log('MainWindow constructed.')
    except Exception as e:
        _debug_log('Exception during MainWindow init:')
        _debug_log(''.join(traceback.format_exception(e)))
        raise
    if preset_to_load:
        try:
            # Only load if file exists to avoid warning popups on silent background start
            from pathlib import Path as _P
            preset_path = PRESETS_DIR / f"{preset_to_load}.yaml"
            if preset_path.exists():
                win.load_preset(preset_to_load)
        except Exception:
            pass
    if not start_hidden:
        _debug_log('Calling win.show()...')
        win.show()
        _debug_log('win.show() returned. Entering event loop...')
    try:
        rc = app.exec()
        _debug_log(f'App exited with code {rc}')
        sys.exit(rc)
    except Exception as e:
        _debug_log('Exception in app.exec():')
        _debug_log(''.join(traceback.format_exception(e)))
        raise

# ---- System-level helpers (Windows specific for media / hotkeys) ----
if sys.platform == 'win32':
    import ctypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    KEYEVENTF_KEYUP = 0x0002
    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_PLAY_PAUSE = 0xB3
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF

    _SPECIAL_KEY_MAP = {
        'ctrl': 0x11,
        'control': 0x11,
        'shift': 0x10,
        'alt': 0x12,
        'win': 0x5B,
        'super': 0x5B,
    }

def _vk_for_token(tok: str):
    t = tok.lower().strip()
    if sys.platform != 'win32':
        return None
    if not t:
        return None
    # Function keys F1-F12
    if t.startswith('f') and t[1:].isdigit():
        num = int(t[1:])
        if 1 <= num <= 12:
            return 0x70 + (num - 1)  # VK_F1 = 0x70
    if t == 'esc' or t == 'escape':
        return 0x1B
    if t in _SPECIAL_KEY_MAP:
        return _SPECIAL_KEY_MAP[t]
    if len(t) == 1:
        return ord(t.upper())
    return None

def _send_vk_sequence(down_then_up):
    if sys.platform != 'win32':
        return
    for vk in down_then_up:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(down_then_up):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

def MainWindow__perform_hotkey(self, combo: str):
    """Execute a hotkey or a sequence of hotkey chords.

    Syntax examples:
      ctrl+shift+esc            (single chord)
      win+d                     (single chord)
      ctrl+alt+del>win+d        (sequence of two chords executed in order)
      f5                        (function key)
      shift+f2>alt+f4           (sequence with function keys)

    Chords are separated by '>' (or ','). Inside a chord, '+' joins simultaneous keys.
    """
    if not combo:
        return
    # Normalize separators: allow '>' or ','
    seq_delims = re.split(r'[>,]', combo)
    chords = [c.strip() for c in seq_delims if c.strip()]
    if not chords:
        return
    for chord in chords:
        parts = [p.strip() for p in chord.split('+') if p.strip()]
        vk_list = []
        for part in parts:
            vk = _vk_for_token(part)
            if vk is not None:
                vk_list.append(vk)
        if vk_list:
            _send_vk_sequence(vk_list)
            # Small delay between chords to mimic natural sequence timing
            QThread.msleep(55)

def MainWindow__perform_media_control(self, action: str, steps: int = 1):
    if sys.platform != 'win32':
        return
    mapping = {
        'next': VK_MEDIA_NEXT_TRACK,
        'previous': VK_MEDIA_PREV_TRACK,
        'play/pause': VK_MEDIA_PLAY_PAUSE,
        'volume up': VK_VOLUME_UP,
        'volume down': VK_VOLUME_DOWN,
        'mute': VK_VOLUME_MUTE,
    }
    vk = mapping.get(action.lower())
    if vk:
        steps = max(1, min(25, int(steps)))
        for _ in range(steps):
            _send_vk_sequence([vk])

# Monkey patch helper methods into class (avoid reorganizing large class body)
MainWindow._perform_hotkey = MainWindow__perform_hotkey  # type: ignore
MainWindow._perform_media_control = MainWindow__perform_media_control  # type: ignore

if __name__ == "__main__":
    main()
