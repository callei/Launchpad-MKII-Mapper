import sys
import subprocess
import os
import shlex
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
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from widgets import PadButton, FramelessPopup, ColorPickerPopup, SettingsPopup, TextInputPopup, PadEditorDialog, AppPickerDialog
import yaml
import re
import traceback
import shutil
import atexit
import signal
import getpass

# Main GUI module for Launchpad Mapper!

DEBUG_MODE = ('--debug' in sys.argv) or os.environ.get('LAUNCHPADMAPPER_DEBUG') == '1'
if '--debug' in sys.argv:
    try:
        sys.argv.remove('--debug')
    except ValueError:
        pass

_LOG_PATH = None
_GLOBAL_WIN = None  # Set to MainWindow instance for global cleanup
def _debug_log(msg: str):
    # Write a line to the debug log if debug mode is active.
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

# Launchpad Mapper GUI.
"""
Fixes applied:
- Adds color-only pad mapping (assign a color without an action), reverted to no color before.
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

# AppData configuration location (Windows). Fallback: project root.
APPDATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LaunchpadMapper"
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APPDATA_DIR / "config.yaml"
PRESETS_DIR = APPDATA_DIR / "presets"

# Security helper functions
def _is_safe_path(base_dir: Path, user_path: Path) -> bool:
    #Validate that user_path is within base_dir to prevent path traversal attacks.
    try:
        # Resolve both paths to absolute, canonical paths
        base = base_dir.resolve()
        target = user_path.resolve()
        # Use relative_to() which raises ValueError if target is not under base
        # This is more secure than string comparison
        target.relative_to(base)
        return True
    except (OSError, ValueError):
        # ValueError raised if target is not relative to base
        return False

# --------- Main Window Class ---------------------------------------------- #

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
        # Load last used preset if recorded; else fallback to 'empty' if present
        try:
            last_preset = None
            ui_section = self.config.get('ui') if isinstance(self.config, dict) else {}
            if isinstance(ui_section, dict):
                last_preset = ui_section.get('last_preset')
            if last_preset and (PRESETS_DIR / f"{last_preset}.yaml").exists():
                self.load_preset(last_preset)
            else:
                empty_path = PRESETS_DIR / "empty.yaml"
                if empty_path.exists():
                    self.load_preset("empty")
        except Exception:
            pass
        # Mark ready so subsequent edits trigger autosave
        self._ready = True
        # Try automatic Launchpad connect after UI shown
        QTimer.singleShot(0, lambda: self.init_midi(auto=True))
        # Start hidden if flag is given
        if start_hidden:
            self.hide()
        # Expose global reference for cleanup handlers
        global _GLOBAL_WIN
        _GLOBAL_WIN = self

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
        # Settings (gear) button
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedWidth(32)
        settings_btn.setStyleSheet(btn_style)
        settings_btn.setToolTip("Open Settings")
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn)
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
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _title_mouse_move(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
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
                    # col may be a hex string or a dict {color: "#rrggbb", intensity: "low|medium|high"}
                    if isinstance(col, dict):
                        btn.update_color(col.get("color"))
                    else:
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
        # Use the enhanced color picker with intensity
        picker = ColorPickerPopup(initial_color=None, original_color=None, parent=self, initial_intensity="medium")
        if picker.exec() == QDialog.Accepted:
            sel = picker.get_selected_color()
            if sel:
                inten = picker.get_selected_intensity()
                frame.setdefault("pads", {})[f"{pad[0]},{pad[1]}"] = {"color": sel, "intensity": inten}
            else:
                frame.setdefault("pads", {}).pop(f"{pad[0]},{pad[1]}", None)
        else:
            # Cancel: no change
            pass
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
                    if isinstance(col, dict):
                        hex_col = col.get('color')
                        inten = str(col.get('intensity', 'medium')).lower()
                    else:
                        hex_col = col
                        inten = 'medium'
                    if not (isinstance(hex_col, str) and len(hex_col) == 7 and hex_col.startswith('#')):
                        continue
                    r = int(hex_col[1:3],16); g=int(hex_col[3:5],16); b=int(hex_col[5:7],16)
                    factor_map = {'low':0.4,'medium':0.7,'high':1.0}
                    f = factor_map.get(inten, 0.7)
                    r = max(0,min(255,int(r*f))); g=max(0,min(255,int(g*f))); b=max(0,min(255,int(b*f)))
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
                    # Apply optional intensity scaling
                    inten = str(mapping.get("intensity", "medium")).lower()
                    factor_map = {"low": 0.4, "medium": 0.7, "high": 1.0}
                    if inten not in factor_map:
                        # support numeric legacy 1..3 (no stray try-block)
                        raw_int = mapping.get("intensity")
                        n: int | None = None
                        if isinstance(raw_int, int):
                            n = raw_int
                        elif isinstance(raw_int, str) and raw_int.isdigit():
                            try:
                                n = int(raw_int)
                            except Exception:
                                n = None
                        if n in (1,2,3):
                            inten = {1:"low", 2:"medium", 3:"high"}[n]
                    f = factor_map.get(inten, 0.7)
                    r = max(0, min(255, int(r * f)))
                    g = max(0, min(255, int(g * f)))
                    b = max(0, min(255, int(b * f)))
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
                /* Transparent buttons – we draw arrows ourselves in CaretSpinStyle */
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
                QMessageBox.information(self, "Launchpad", f"No Launchpad found (autoconnect failed).\nYou can plug it in and click 'Connect Launchpad'.\n\nError: {e}")
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
        # Minimize-to-tray behavior if enabled
        try:
            ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
            minimize_to_tray = bool(ui.get('minimize_to_tray', False))
        except Exception:
            minimize_to_tray = False
        if minimize_to_tray and not getattr(self, '_force_quit', False):
            event.ignore()
            self.hide()
            # One-time notice
            if not getattr(self, '_tray_tip_shown', False) and hasattr(self, 'tray'):
                try:
                    self.tray.showMessage("Launchpad Mapper", "Still running in tray. Use Quit to exit.")
                    self._tray_tip_shown = True
                except Exception:
                    pass
            # Persist hidden state quickly
            try:
                ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
                if isinstance(ui, dict):
                    ui['start_hidden'] = True
                    self.config['ui'] = ui
                self.save_config(silent=True)
            except Exception:
                pass
            return
        # Normal close path
        try:
            ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
            if isinstance(ui, dict):
                ui['start_hidden'] = False
                self.config['ui'] = ui
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
        # Try to use app.ico if packed
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
        settings_act = QAction("Settings", self)
        settings_act.triggered.connect(self._open_settings)
        menu.addAction(settings_act)
        reload_lp = QAction("Reconnect Launchpad", self)
        reload_lp.triggered.connect(lambda: self.init_midi(auto=False))
        menu.addAction(reload_lp)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self._tray_quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._tray_tip_shown = False
        self._force_quit = False

    def _tray_show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        # Ensure next start isn't forced hidden just because we opened it
        try:
            ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
            if isinstance(ui, dict):
                ui['start_hidden'] = False
                self.config['ui'] = ui
                self.save_config(silent=True)
        except Exception:
            pass

    def _tray_quit(self):
        self._force_quit = True
        self.close()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isHidden():
                self._tray_show()
            else:
                self.hide()

    # --- Settings dialog integration ---
    def _open_settings(self):
        try:
            ui_cfg = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
        except Exception:
            ui_cfg = {}
        dlg = SettingsPopup(ui_cfg, getattr(sys, 'frozen', False), parent=self)
        if dlg.exec() == QDialog.Accepted:
            res = dlg.get_result()
            try:
                ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
                if not isinstance(ui, dict):
                    ui = {}
                mode = (res.get('start_mode') or 'normal').lower()
                if mode not in ('normal','minimized','hidden'):
                    mode = 'normal'
                # Always persist start_mode; manual launches ignore it (applied only if autostart)
                ui['start_mode'] = mode
                ui['minimize_to_tray'] = bool(res.get('minimize_to_tray'))
                ui['autostart_enabled'] = bool(res.get('autostart_enabled'))
                ui['start_hidden'] = (mode == 'hidden') and bool(res.get('autostart_enabled'))
                self.config['ui'] = ui
                # Update Windows autostart
                if sys.platform == 'win32':
                    ok = self._update_autostart_registry(ui['autostart_enabled'])
                    if not ok and ui['autostart_enabled']:
                        QMessageBox.warning(self, 'Autostart', 'Failed to update Windows autostart registry.')
                self.save_config(silent=True)
                # Apply mode immediately
                if not ui['autostart_enabled']:
                    # Manual run always shown normal regardless of chosen mode when autostart is off
                    self.showNormal(); self.raise_(); self.activateWindow()
                elif mode == 'normal':
                    self.showNormal(); self.raise_(); self.activateWindow()
                elif mode == 'minimized':
                    self.show(); self.showMinimized()
                elif mode == 'hidden':
                    self.hide()
                self.statusBar().showMessage('Settings saved.', 4000)
            except Exception as e:
                QMessageBox.warning(self, 'Settings', f'Failed to save settings: {e}')

    def _update_autostart_registry(self, enable: bool) -> bool:
        if sys.platform != 'win32':
            return False
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
                value_name = 'LaunchpadMapper'
                if enable:
                    if getattr(sys, 'frozen', False):
                        exe_path = Path(sys.executable).resolve()
                        # Pass an explicit flag so the app knows this was an autostart launch
                        cmd = f'"{exe_path}" --autostart'
                    else:
                        # Start via python executable to ensure correct interpreter in dev mode
                        py = Path(sys.executable).resolve()
                        script = Path(__file__).resolve()
                        cmd = f'"{py}" "{script}" --autostart'
                    winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, value_name)
                    except FileNotFoundError:
                        pass
            return True
        except Exception:
            return False

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
        """Load a layer-scoped preset: replace only current layer's pads.
        
        Security: Validates path is within PRESETS_DIR to prevent path traversal.
        """
        path = PRESETS_DIR / f"{name}.yaml"
        # Security: Validate path is within allowed directory
        if not _is_safe_path(PRESETS_DIR, path):
            QMessageBox.warning(self, "Security Error", "Invalid preset path.")
            return
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
        # Persist last_preset in config ui section for auto-load on next start
        try:
            ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
            if not isinstance(ui, dict):
                ui = {}
            ui['last_preset'] = name
            self.config['ui'] = ui
            self.save_config(silent=True)
        except Exception:
            pass
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
            args = mapping.get("args", "")
            if path:
                try:
                    self._launch_app_silent(path, args=args)
                except Exception as e:
                    QMessageBox.warning(self, "Launch App", f"Failed to launch: {e}")
        elif mapping and mapping.get("type") == "run process":
            cmd = mapping.get("command")
            if cmd:
                try:
                    # Security: Use shell=False to prevent command injection
                    # Parse command string safely using shlex
                    try:
                        cmd_list = shlex.split(cmd, posix=(sys.platform != 'win32'))
                        subprocess.Popen(cmd_list, shell=False)
                    except ValueError:
                        # If shlex fails, try simple split as fallback but still no shell
                        cmd_list = cmd.split()
                        subprocess.Popen(cmd_list, shell=False)
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
                        new_map["args"] = dlg.launch_args.text().strip()
                    elif type_ == "hotkey":
                        new_map["keys"] = dlg.hotkey_edit.text().strip()
                    elif type_ == "media control":
                        new_map["action"] = dlg.media_select.currentText()
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
                    # Store intensity when a color is set
                    inten = (dlg.intensity_select.currentText() or "medium").lower()
                    if inten not in ("low","medium","high"):
                        inten = "medium"
                    new_map["intensity"] = inten
                # Do not persist a color-only mapping without a color value
                if new_map.get("type") == "color" and not color:
                    self.layers[self.active_layer]["pads"].pop(pad_key, None)
                else:
                    self.layers[self.active_layer]["pads"][pad_key] = new_map
            self.update_grid()
            self.sync_pad_lights()
            if self._ready:
                self.mark_dirty()
    def _launch_app_silent(self, path: str, args: str = ""):
        """Launch an app with optional arguments without opening a console window.
        
        Security: Validates path exists and uses shell=False to prevent command injection.
        """
        # Security: Validate that the path exists and is a file
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Application not found: {path}")
        
        # Use subprocess.DEVNULL for automatic resource management
        # subprocess.DEVNULL is available in Python 3.3+ (app requires 3.11+)
        kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        if sys.platform == 'win32':
            CREATE_NO_WINDOW = 0x08000000
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs['startupinfo'] = si
            kwargs['creationflags'] = CREATE_NO_WINDOW
        # Build command
        cmd = [path]
        if args:
            # Parse arguments safely for Windows compatibility
            try:
                parts = shlex.split(args, posix=False)
                cmd.extend(parts)
            except Exception:
                # fallback: pass as single parameter
                cmd.append(args)
        try:
            subprocess.Popen(cmd, shell=False, **kwargs)
        except Exception as e:
            # Fallback to plain execution (still with shell=False for security)
            try:
                subprocess.Popen([path], shell=False, **kwargs)
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
        # Ensure UI section exists; keep custom fields
        try:
            ui = self.config.get('ui', {}) if isinstance(self.config, dict) else {}
            if not isinstance(ui, dict):
                ui = {}
            # Maintain start_mode preference; derive start_hidden for backward compatibility
            ui.setdefault('start_mode', 'normal')
            if ui.get('start_mode') == 'hidden' or self.isHidden():
                ui['start_hidden'] = True
            else:
                ui['start_hidden'] = False
            # Preserve existing flags if present
            ui.setdefault('minimize_to_tray', ui.get('minimize_to_tray', False))
            ui.setdefault('autostart_enabled', ui.get('autostart_enabled', False))
            # last_preset may already be set elsewhere
            ui.setdefault('last_preset', ui.get('last_preset'))
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
    """Draws clear white triangles (up/down) + combobox arrow against transparent background."""
    def drawPrimitive(self, element, option, painter, widget=None):
        from PySide6.QtGui import QPolygon
        targets = (QStyle.PE_IndicatorSpinUp, QStyle.PE_IndicatorSpinDown, QStyle.PE_IndicatorArrowDown)
        if element in targets:
            painter.save()
            r = option.rect.adjusted(2, 2, -2, -2)
            painter.setRenderHint(QPainter.Antialiasing, True)
            # Faint hover background
            if option.state & QStyle.State_MouseOver:
                painter.fillRect(option.rect, QColor(255,255,255,28))
            # Triangle points
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


def main():
    # Determine startup mode: apply config only for autostart; manual runs default to normal
    is_autostart = False
    if '--autostart' in sys.argv:
        is_autostart = True
        try:
            sys.argv.remove('--autostart')
        except ValueError:
            pass
    # Dev/legacy override: allow hidden via flag when running from source
    cli_hidden = False
    if '--background' in sys.argv or '--hidden' in sys.argv:
        cli_hidden = True
        try:
            sys.argv.remove('--background')
        except ValueError:
            pass
        try:
            sys.argv.remove('--hidden')
        except ValueError:
            pass
    saved_mode = 'normal'
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, 'r', encoding='utf-8') as _cf:
                _cfg = yaml.safe_load(_cf) or {}
            if isinstance(_cfg, dict):
                ui = _cfg.get('ui') or {}
                if isinstance(ui, dict):
                    saved_mode = ui.get('start_mode') or ('hidden' if ui.get('start_hidden') else 'normal')
    except Exception:
        pass
    if cli_hidden:
        effective_mode = 'hidden'
    elif is_autostart:
        effective_mode = saved_mode if saved_mode in ('normal','minimized','hidden') else 'normal'
    else:
        effective_mode = 'normal'
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
        _debug_log('QApplication created. Checking single-instance...')
        # --- Single-instance guard using QLocalServer ---
        server_name = f"LaunchpadMapper_{getpass.getuser()}"
        # If another instance is listening, signal it to show and exit
        try:
            probe = QLocalSocket()
            probe.connectToServer(server_name)
            if probe.waitForConnected(200):
                try:
                    probe.write(b'SHOW')
                    probe.flush()
                    probe.waitForBytesWritten(200)
                except Exception:
                    pass
                try:
                    probe.disconnectFromServer()
                except Exception:
                    pass
                _debug_log('Another instance detected; signaled SHOW and exiting.')
                sys.exit(0)
        except Exception:
            pass
        _debug_log('Constructing MainWindow...')
        win = MainWindow(start_hidden=(effective_mode == 'hidden'))
        _debug_log('MainWindow constructed.')
        # Start local server to receive SHOW requests from subsequent launches
        try:
            QLocalServer.removeServer(server_name)
        except Exception:
            pass
        try:
            server = QLocalServer(app)
            def _on_new_conn():
                try:
                    sock = server.nextPendingConnection()
                except Exception:
                    sock = None
                # Regardless of message, bring to foreground
                try:
                    win._tray_show()
                except Exception:
                    try:
                        win.showNormal(); win.raise_(); win.activateWindow()
                    except Exception:
                        pass
                # Read/close socket
                if sock is not None:
                    try:
                        # drain any bytes
                        _ = sock.readAll()
                    except Exception:
                        pass
                    try:
                        sock.disconnectFromServer()
                    except Exception:
                        pass
            if server.listen(server_name):
                try:
                    server.newConnection.connect(_on_new_conn)
                    # Keep reference on window to avoid GC
                    setattr(win, '_single_server', server)
                except Exception:
                    pass
        except Exception:
            pass
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
    if effective_mode != 'hidden':
        _debug_log('Calling win.show()...')
        win.show()
        if effective_mode == 'minimized':
            win.showMinimized()
        _debug_log('win.show() returned. Entering event loop...')
    # --- Cleanup hooks (best-effort) ---
    def _cleanup_leds_and_ports():
        try:
            w = _GLOBAL_WIN
            if w and getattr(w, 'lp', None):
                try:
                    w.lp.clear(full=True)
                except Exception:
                    pass
                try:
                    w.lp.close()
                except Exception:
                    pass
        except Exception:
            pass
    try:
        app.aboutToQuit.connect(_cleanup_leds_and_ports)
    except Exception:
        pass
    try:
        atexit.register(_cleanup_leds_and_ports)
    except Exception:
        pass
    def _signal_handler(signum, frame):
        _cleanup_leds_and_ports()
        try:
            sys.exit(0)
        except SystemExit:
            raise
        except Exception:
            pass
    for _sig_name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        s = getattr(signal, _sig_name, None)
        if s is not None:
            try:
                signal.signal(s, _signal_handler)
            except Exception:
                pass
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
    """Execute a hotkey or a sequence of hotkey chords i.e. a macro!

    # NOTE: If you want to allow advanced shell features (such as pipes, redirection, or shell built-ins),
    # you must change the implementation to use shell=True and pass the command as a string to subprocess.
    # Be aware that this introduces significant security risks (command injection).
    # Only do this if you fully trust the source of the commands.

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
    # Normalize separators: allow '>' or ','.
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
