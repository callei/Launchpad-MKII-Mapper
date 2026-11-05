import sys
import os
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QGridLayout,
    QPushButton,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QHBoxLayout,
    QFileDialog,
    QDialog,
    QInputDialog,
    QTabWidget,
    QSpinBox,
    QColorDialog,
)
from PySide6.QtCore import Qt, Signal, Slot, QPoint, QTimer
from PySide6.QtGui import QPalette, QColor, QFont, QFontDatabase, QPainter, QPen, QIcon, QAction
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleFactory, QSystemTrayIcon, QMenu
from PySide6.QtWidgets import QGraphicsDropShadowEffect
from PySide6.QtNetwork import QLocalServer, QLocalSocket
import math
import time
import traceback
import atexit
import signal
import getpass

# Ensure project root is on sys.path for direct script execution (widgets imports launchpad too)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from widgets import (  # noqa: E402
    PadButton,
    ColorPickerPopup,
    SettingsPopup,
    TextInputPopup,
    PadEditorDialog,
    show_message,
    ask_yes_no,
)

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

# MIDI/Launchpad integration
try:
    from launchpad.controller import LaunchpadController
except ImportError:
    LaunchpadController = None
from launchpad import config_store  # noqa: E402
from launchpad.config_store import ConfigStore, PresetError, UnsafePresetName  # noqa: E402
from launchpad import mapping as padmap  # noqa: E402
from launchpad.mapping import PadMapping, pad_key, parse_pad_key  # noqa: E402
from launchpad.animation import AnimationEngine, Tick  # noqa: E402
from launchpad.leds import LedPainter  # noqa: E402
from launchpad.layers import LayerSet  # noqa: E402
from launchpad.actions import ActionRunner, SystemAdapter  # noqa: E402
from launchpad import port as lp_port  # noqa: E402

# AppData configuration location (Windows). Fallback: project root.
APPDATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LaunchpadMapper"
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APPDATA_DIR / "config.yaml"
PRESETS_DIR = APPDATA_DIR / "presets"

# Shipped presets copied into PRESETS_DIR on first run (dev tree, frozen onedir, or its _internal dir)
_EXE_DIR = Path(sys.executable).resolve().parent
PRESET_SEED_DIRS = [PROJECT_ROOT / 'presets', _EXE_DIR / 'presets', _EXE_DIR / '_internal' / 'presets']


def user_files_dir() -> Path:
    """Where export/import dialogs start: Documents, not the install dir (Program Files needs admin)."""
    docs = Path.home() / 'Documents'
    return docs if docs.is_dir() else Path.home()


def app_version() -> str:
    """pyproject.toml's version; a frozen exe reads its own version resource (build_exe.ps1 sets it from there)."""
    try:
        if getattr(sys, 'frozen', False) and sys.platform == 'win32':
            import win32api

            info = win32api.GetFileVersionInfo(sys.executable, '\\')
            ms, ls = info['FileVersionMS'], info['FileVersionLS']
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"
        import tomllib

        with open(PROJECT_ROOT / 'pyproject.toml', 'rb') as f:
            return tomllib.load(f)['project']['version']
    except Exception:
        return "unknown"


# --------- Main Window Class ---------------------------------------------- #


class MainWindow(QMainWindow):
    # Emitted from the rtmidi thread; queued onto the GUI thread
    _midi_pressed = Signal(int, int)
    _midi_released = Signal(int, int)

    def __init__(self, start_hidden: bool = False):
        super().__init__()
        self.theme_name = "Minimalistic Black"
        # Frameless window for a slick look
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setWindowTitle("")  # Remove default title text (taskbar icon still shows app name)
        self.resize(860, 540)
        self._drag_pos: QPoint | None = None
        self.current_animation = None
        self.current_frame_index = 0
        # Last color + intensity picked in either editor; pickers for uncolored pads start on it
        self._last_color: str | None = None
        self._last_intensity = "medium"
        # Animation playback: the engine plays runs, one timer ticks it, the painter writes the LEDs
        self.anim = AnimationEngine()
        self.leds = LedPainter()
        self._anim_timer = QTimer(self)
        self._anim_timer.setSingleShot(True)
        self._anim_timer.setTimerType(Qt.PreciseTimer)
        self._anim_timer.timeout.connect(self._animate)
        self.current_preset_name = None  # Track which preset is currently loaded
        # Layers and the active layer; init_ui subscribes the list widget and grid to its changes
        self.layer_set = LayerSet()
        # OS actions (launch/run/hotkey/media); later chords of a hotkey sequence are timed by the event loop
        self.action_runner = ActionRunner(SystemAdapter(), defer=QTimer.singleShot)
        # The Launchpad connection; presses arrive on the rtmidi thread, so only emit signals there.
        # The factory looks LaunchpadController up at connect time (tests swap it in after construction).
        self.port = lp_port.LaunchpadPort(lambda: LaunchpadController())
        self.port.on_press = lambda pad: self._midi_pressed.emit(pad[0], pad[1])
        self.port.on_release = lambda pad: self._midi_released.emit(pad[0], pad[1])
        self.port.subscribe(self._on_port_state)
        self._port_was_lost = False
        # Hot-plug: init_midi starts this; it notices unplugs and reconnects on replug
        self._port_poll_timer = QTimer(self)
        self._port_poll_timer.setInterval(2000)
        self._port_poll_timer.timeout.connect(self.port.poll)
        # Internal dirty tracking for debounced autosave
        self._dirty = False
        self._save_debounce_timer = None
        # Persistence: config.yaml + presets/ (load the saved config, incl. animations, before building UI)
        self.store = ConfigStore(APPDATA_DIR, seed_dirs=PRESET_SEED_DIRS)
        try:
            self.store.ensure_presets()
        except OSError:
            pass
        loaded = self.store.load()
        # Falls back to defaults (main layer + nav pads) when there is no readable config.yaml.
        # animations: name -> {frames:[{duration:int, pads:{"x,y":"#rrggbb"}}]}
        self._apply_config(loaded.config, dirty=False)
        config_loaded = loaded.loaded
        # Flag to suppress autosave during initial construction
        self._ready = False
        # Connect signals for thread-safe UI updates
        self._midi_pressed.connect(self._on_midi_pressed)
        self._midi_released.connect(self._on_midi_released)
        self.custom_font_family = None
        self.load_custom_font()
        self.init_ui()
        self.apply_theme()
        self._install_tray()
        # Fresh install only: config.yaml already holds the user's pads, and loading a preset would replace them
        if not config_loaded and self.store.has_preset("empty"):
            try:
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
        close_btn.setStyleSheet(
            btn_style + "QPushButton { color:#e66; } QPushButton:hover { background:#a33; color:#fff; }"
        )
        close_btn.clicked.connect(self.close)
        layout.addWidget(min_btn)
        layout.addWidget(close_btn)
        bar.setLayout(layout)
        # Enable dragging by installing mouse events
        bar.mousePressEvent = self._title_mouse_press  # type: ignore
        bar.mouseMoveEvent = self._title_mouse_move  # type: ignore
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

    @property
    def layers(self) -> dict:
        """Read-only view: the layer set's live layers dict."""
        return self.layer_set.layers

    @property
    def active_layer(self) -> str:
        return self.layer_set.active

    def get_pad_mapping(self, pad):
        return self.layer_set.active_pads.get(pad_key(pad), None)

    def export_config(self):
        fname, _ = QFileDialog.getSaveFileName(
            self, "Export Config", str(self.store.config_path), "YAML Files (*.yaml);;JSON Files (*.json)"
        )
        if fname:
            try:
                config_store.export_file(self.config, fname)
            except Exception as e:
                show_message(self, "Export", f"Failed to export: {e}")
                return
            show_message(self, "Exported", f"Config exported to {fname}")

    def import_config(self):
        self._load_config_file("Import Config", "YAML Files (*.yaml);;JSON Files (*.json)")

    def load_config_dialog(self):
        self._load_config_file("Load Config", "YAML Files (*.yaml)")

    def _load_config_file(self, title: str, file_filter: str):
        fname, _ = QFileDialog.getOpenFileName(self, title, str(self.store.config_path), file_filter)
        if not fname:
            return
        result = self.store.load(fname)
        if not result.loaded:
            show_message(self, title, "\n".join(result.problems))
            return
        self._apply_config(result.config)

    def _apply_config(self, config: dict, dirty: bool = True):
        """Make a loaded config the live one: stop animations, rebind state, rebuild the layer list, repaint."""
        self._stop_animation_playback()
        self.config = config
        self.animations = config["animations"]
        # Rebuilds the layer list and repaints via _on_layers_changed (once init_ui has subscribed it)
        self.layer_set.replace(config["layers"], config["active_layer"])
        for i, color in enumerate(config_store.custom_colors(config)):
            QColorDialog.setCustomColor(i, QColor(color))
        if dirty:
            self.mark_dirty()

    def _sync_custom_colors(self):
        """Save the color picker's custom slots if a picker changed them (they live in QColorDialog)."""
        current = [QColorDialog.customColor(i).name() for i in range(config_store.CUSTOM_COLOR_SLOTS)]
        if current != config_store.custom_colors(self.config):
            config_store.ui_settings(self.config)['custom_colors'] = current
            self.mark_dirty()

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
            "<b>Pad Animation:</b> Physical pad presses render a thick border in the UI.<br><br>"
            f"Version {app_version()} &middot; by "
            '<a href="https://github.com/callei">callei</a>'
        )
        # A plain dialog, not QMessageBox.information: that plays the Windows notification sound
        dlg = QDialog(self)
        dlg.setWindowTitle("Help/About")
        label = QLabel(help_text)
        label.setOpenExternalLinks(True)
        ok = QPushButton("OK")
        ok.clicked.connect(dlg.accept)
        layout = QVBoxLayout(dlg)
        layout.addWidget(label)
        layout.addWidget(ok, alignment=Qt.AlignRight)
        dlg.exec()

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

        # Top control row (y=-1) in row 0, the 8x8 matrix below it, side column (x=8) in column 8
        for pad in padmap.ALL_PADS:
            x, y = pad
            label = "◀" if pad == (2, -1) else ("▶" if pad == (3, -1) else "")
            btn = PadButton(x, y, label=label, control=(y == -1 or x == 8))
            btn.clicked.connect(lambda checked, p=pad: self.edit_pad(p))
            grid.addWidget(btn, y + 1, x)
            self.pad_buttons[pad] = btn
        self.update_grid()

        grid_widget.setLayout(grid)
        main_layout.addWidget(grid_widget)

        # --- Sidebar ---
        sidebar_widget = QWidget()
        sidebar_widget.setObjectName("sideBar")
        sidebar = QVBoxLayout()

        self.layer_list = QListWidget()
        self._sync_layer_list()
        self.layer_list.currentTextChanged.connect(self.switch_layer)
        self.layer_set.subscribe(self._on_layers_changed)
        sidebar.addWidget(QLabel("Layers:"))
        sidebar.addWidget(self.layer_list)

        # Layer management
        layer_btn_row = QHBoxLayout()
        self.add_layer_btn = QPushButton("+")
        self.add_layer_btn.setToolTip("Add layer")
        self.add_layer_btn.clicked.connect(self.add_layer_dialog)
        self.del_layer_btn = QPushButton("−")
        self.del_layer_btn.setToolTip("Delete layer")
        self.del_layer_btn.clicked.connect(self.delete_current_layer)
        self.clear_layer_btn = QPushButton("Clear")
        self.clear_layer_btn.setToolTip("Clear layer (keeps the ◀ ▶ layer-nav pads)")
        self.clear_layer_btn.clicked.connect(self.clear_current_layer)
        layer_btn_row.addWidget(self.add_layer_btn)
        layer_btn_row.addWidget(self.del_layer_btn)
        layer_btn_row.addWidget(self.clear_layer_btn)
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
            self.config_btn,
            self.presets_btn,
            self.help_btn,
            self.connect_btn,
            self.add_layer_btn,
            self.del_layer_btn,
            self.clear_layer_btn,
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
        # Same layout as the mappings grid
        for pad in padmap.ALL_PADS:
            x, y = pad
            btn = PadButton(x, y, label="", color=None, control=(y == -1 or x == 8))
            btn.clicked.connect(lambda checked, p=pad: self._edit_animation_pad(p))
            g.addWidget(btn, y + 1, x)
            self.anim_pad_buttons[pad] = btn
        self.anim_grid_widget.setLayout(g)
        right.addWidget(self.anim_grid_widget)
        # Paint mode: clicks apply the brush directly (clicking a pad that already has it clears it)
        paint_row = QHBoxLayout()
        self.paint_btn = QPushButton("Paint")
        self.paint_btn.setCheckable(True)
        self.paint_btn.setToolTip("Click pads to give them the brush color; click again to clear")
        self.brush_btn = QPushButton()
        self.brush_btn.setToolTip("Pick the brush color and strength")
        self.brush_btn.clicked.connect(self._pick_brush)
        paint_row.addWidget(self.paint_btn)
        paint_row.addWidget(self.brush_btn, 1)
        right.addLayout(paint_row)
        self._brush = ("#ff0000", "high")
        self._show_brush()
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
            show_message(self, "Animation", "Name exists.")
            return
        self.animations[n] = {"frames": []}
        self.animation_list.addItem(n)
        self.animation_list.setCurrentRow(self.animation_list.count() - 1)
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
            show_message(self, "Animation", "Name already exists.")
            return
        # Move frames data
        data = self.animations.pop(old)
        self.animations[new_name] = data
        # Update any pad mappings referencing the old name
        for layer_data in self.layers.values():
            pads = layer_data.get("pads", {}) if isinstance(layer_data, dict) else {}
            for mapping in pads.values():
                if isinstance(mapping, dict) and mapping.get("type") == padmap.ANIMATION and mapping.get("name") == old:
                    mapping["name"] = new_name
        # Running runs follow the rename, so their pad can still stop them
        self.anim.rename(old, new_name)
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
        if not ask_yes_no(self, "Delete Animation", f"Delete '{self.current_animation}'?"):
            return
        self.animations.pop(self.current_animation, None)
        # A deleted loop-forever animation could otherwise never be stopped
        self.anim.stop(self.current_animation)
        self._animate()
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
            self.frame_list.addItem(f"Frame {i + 1} ({fr.get('duration', 300)}ms)")
        if frames:
            self.frame_list.setCurrentRow(0)

    def _add_frame(self):
        if not self.current_animation:
            return
        frames = self.animations[self.current_animation]["frames"]
        frames.append({"duration": self.frame_duration_spin.value(), "pads": {}})
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(self.frame_list.count() - 1)
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

        frames.insert(idx + 1, copy.deepcopy(frames[idx]))
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(idx + 1)
        self.mark_dirty()

    def _move_frame_up(self):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        if idx <= 0:
            return
        frames = self.animations[self.current_animation]["frames"]
        frames[idx - 1], frames[idx] = frames[idx], frames[idx - 1]
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(idx - 1)
        self.mark_dirty()

    def _move_frame_down(self):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        frames = self.animations[self.current_animation]["frames"]
        if idx < 0 or idx >= len(frames) - 1:
            return
        frames[idx + 1], frames[idx] = frames[idx], frames[idx + 1]
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(idx + 1)
        self.mark_dirty()

    def _on_frame_selected(self, row):
        self._refresh_animation_grid()
        if self.current_animation and row >= 0:
            frames = self.animations[self.current_animation]["frames"]
            if row < len(frames):
                # Showing a frame's duration is not an edit: don't write it back via valueChanged
                self.frame_duration_spin.blockSignals(True)
                self.frame_duration_spin.setValue(frames[row].get("duration", 300))
                self.frame_duration_spin.blockSignals(False)

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
            btn = self.anim_pad_buttons.get(parse_pad_key(key))
            if btn:
                # col may be a hex string or a dict {color: "#rrggbb", intensity: "low|medium|high"}
                btn.update_color(padmap.screen_hex(col))

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
        if self.paint_btn.isChecked():
            self._paint_animation_pad(frame, pad)
            return
        # Start on this pad's color, else on the last color picked
        cell = frame.get("pads", {}).get(pad_key(pad))
        if isinstance(cell, dict) and cell.get("color"):
            color, inten = cell["color"], padmap.intensity_name(cell.get("intensity", "medium"))
        elif isinstance(cell, str):
            color, inten = cell, "medium"
        else:
            color, inten = self._last_color, self._last_intensity
        picker = ColorPickerPopup(initial_color=color, original_color=color, parent=self, initial_intensity=inten)
        accepted = picker.exec() == QDialog.Accepted
        self._sync_custom_colors()
        if accepted:
            sel = picker.get_selected_color()
            if sel:
                inten = picker.get_selected_intensity()
                frame.setdefault("pads", {})[pad_key(pad)] = {"color": sel, "intensity": inten}
                self._last_color, self._last_intensity = sel, inten
            else:
                frame.setdefault("pads", {}).pop(pad_key(pad), None)
        else:
            # Cancel: no change
            pass
        self._refresh_animation_grid()
        self._refresh_frames_list()
        self.frame_list.setCurrentRow(idx)
        self.mark_dirty()

    def _paint_animation_pad(self, frame, pad):
        pads = frame.setdefault("pads", {})
        color, inten = self._brush
        brush_cell = {"color": color, "intensity": inten}
        if pads.get(pad_key(pad)) == brush_cell:
            pads.pop(pad_key(pad))
        else:
            pads[pad_key(pad)] = brush_cell
        self._refresh_animation_grid()
        self.mark_dirty()

    def _pick_brush(self):
        color, inten = self._brush
        picker = ColorPickerPopup(initial_color=color, original_color=color, parent=self, initial_intensity=inten)
        accepted = picker.exec() == QDialog.Accepted
        self._sync_custom_colors()
        if accepted and picker.get_selected_color():
            self._brush = (picker.get_selected_color(), picker.get_selected_intensity())
            self._last_color, self._last_intensity = self._brush
            self._show_brush()
            self.paint_btn.setChecked(True)  # picking a brush means you want to paint

    def _show_brush(self):
        color, inten = self._brush
        r, g, b = padmap.color_rgb({"color": color, "intensity": inten}, screen=True)
        fg = "#000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#fff"
        self.brush_btn.setText(f"Brush: {color} · {inten}")
        self.brush_btn.setStyleSheet(f"background-color: rgb({r},{g},{b}); color: {fg};")

    def _update_frame_duration(self, val):
        if not self.current_animation:
            return
        idx = self.frame_list.currentRow()
        if idx < 0:
            return
        frames = self.animations[self.current_animation]["frames"]
        if idx < len(frames):
            frames[idx]["duration"] = val
            # Relabel in place: rebuilding the list while the spinbox is being typed in re-entered this
            # handler through the selection change and overflowed the stack
            self.frame_list.item(idx).setText(f"Frame {idx + 1} ({val}ms)")
            self.mark_dirty()

    def _play_selected_animation(self):
        if not self.current_animation:
            return
        self._play_animation(self.current_animation, 1)

    def _play_animation(self, name: str, iterations: int, hold: str = padmap.HOLD_CLEAR):
        """Start `name` for `iterations` cycles (0 or less: loop; starting a running loop stops it).

        `hold` is what stays lit after the last cycle (see mapping.HOLD_MODES).
        """
        if name not in self.animations:
            return
        self.anim.start(name, self.animations[name].get("frames", []), iterations, self._now(), hold)
        self._animate()

    def _stop_animation_playback(self):
        """Stop every animation; their pads go back to the base colors."""
        self.anim.stop()
        self._animate()

    @staticmethod
    def _now() -> float:
        return time.monotonic() * 1000

    def _animate(self):
        """Advance animations to now, paint the result and schedule the next change (the only tick chain)."""
        now = self._now()
        try:
            tick = self.anim.advance(now)
        except Exception:
            # A failed tick would never reschedule; stop cleanly so new animations can start again
            _debug_log(f"Animation tick failed:\n{traceback.format_exc()}")
            self.anim.stop()
            tick = Tick(overlay={}, next_due=None)
        self.leds.paint(overlay=tick.overlay)
        if tick.next_due is None:
            self._anim_timer.stop()
        else:
            self._anim_timer.start(max(0, math.ceil(tick.next_due - now)))

    def _export_animations(self):
        fname, _ = QFileDialog.getSaveFileName(
            self, "Export Animations", str(user_files_dir() / "animations.yaml"), "YAML Files (*.yaml)"
        )
        if not fname:
            return
        try:
            config_store.export_file(self.animations, fname)
            show_message(self, "Animations", f"Exported animations to {fname}")
        except PermissionError:
            show_message(
                self,
                "Animations",
                f"No permission to write to {Path(fname).parent}.\n\nPick a folder you own, such as Documents.",
            )
        except Exception as e:
            show_message(self, "Animations", f"Failed to export: {e}")

    def _import_animations(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Import Animations", str(user_files_dir()), "YAML Files (*.yaml)")
        if not fname:
            return
        try:
            data = config_store.read_mapping(fname)
            # Merge or overwrite? We'll merge keys and overwrite conflicts
            self.animations.update(data)
            # Refresh animations list in UI
            self.animation_list.clear()
            self.animation_list.addItems(sorted(self.animations.keys()))
            show_message(self, "Animations", f"Imported animations from {fname}")
        except Exception as e:
            show_message(self, "Animations", f"Failed to import: {e}")

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
            palette.setColor(QPalette.Window, QColor(30, 30, 32))
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(24, 24, 26))
            palette.setColor(QPalette.AlternateBase, QColor(40, 40, 44))
            palette.setColor(QPalette.ToolTipBase, Qt.white)
            palette.setColor(QPalette.ToolTipText, Qt.white)
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(45, 45, 50))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.Highlight, QColor(70, 120, 200))
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
                """
                % (self.custom_font_family or "Segoe UI")
            )

    def add_layer_dialog(self):
        name, ok = QInputDialog.getText(self, "Add Layer", "Layer name:")
        if not ok or not name.strip():
            return
        # New layers start with the default nav pads and become active (list and grid follow the model)
        if not self.layer_set.add(name.strip()):
            show_message(self, "Layer", "Layer already exists.")
            return
        self.mark_dirty()

    def delete_current_layer(self):
        current = self.active_layer
        if current == "main":
            show_message(self, "Layer", "Cannot delete the primary 'main' layer.")
            return
        if not ask_yes_no(self, "Delete Layer", f"Delete layer '{current}'?"):
            return
        # Falls back to 'main' (else the first layer); the model refuses to delete the last layer
        if not self.layer_set.delete(current):
            show_message(self, "Layer", "Cannot delete the last layer.")
            return
        self.mark_dirty()

    def clear_current_layer(self):
        current = self.active_layer
        if not ask_yes_no(self, "Clear Layer", f"Remove every pad mapping on layer '{current}'?"):
            return
        self.layer_set.clear(current)  # repaints via _on_layers_changed
        self.mark_dirty()

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

    def init_midi(self, auto: bool = False):
        if not LaunchpadController:
            if not auto:
                show_message(
                    self,
                    "MIDI",
                    "Launchpad backend not available. Install dependencies:\npip install mido python-rtmidi",
                )
            return
        self.port.connect()
        self._port_poll_timer.start()
        if self.port.state == lp_port.VIRTUAL and not auto:
            # Only on explicit Connect/Reconnect; the automatic attempt at startup (possibly hidden) stays quiet
            show_message(
                self,
                "Launchpad",
                f"{self.port.reason}\n\nRunning in virtual mode. Plugging the Launchpad in reconnects it; "
                "otherwise fix this, then click 'Connect Launchpad' again.",
            )

    def _on_port_state(self):
        state, reason = self.port.state, self.port.reason
        if state == lp_port.CONNECTED:
            if self._port_was_lost:
                self._report_action_error("Launchpad reconnected")
            else:
                self.statusBar().showMessage(reason, 5000)
            # A new controller's LEDs are unknown: attaching makes this paint every pad
            self.leds.attach(self.port)
            self.leds.paint()
        elif state == lp_port.LOST:
            self._report_action_error(f"{reason} Plug it back in to reconnect.")
        else:
            self.statusBar().showMessage(f"Virtual mode: {reason}", 15000)
        self._port_was_lost = state == lp_port.LOST

    def mark_dirty(self):
        self._dirty = True
        if self._save_debounce_timer is None:
            self._save_debounce_timer = QTimer(self)
            self._save_debounce_timer.setSingleShot(True)
            self._save_debounce_timer.timeout.connect(lambda: self.save_config(silent=True))
        self._save_debounce_timer.start(800)

    def closeEvent(self, event):
        # Minimize-to-tray behavior if enabled
        minimize_to_tray = bool(config_store.ui_settings(self.config).get('minimize_to_tray', False))
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
                config_store.ui_settings(self.config)['start_hidden'] = True
                self.save_config(silent=True)
            except Exception:
                pass
            return
        # Normal close path
        try:
            config_store.ui_settings(self.config)['start_hidden'] = False
            self.save_config(silent=True)
        except Exception:
            pass
        # Ensure hardware LEDs are cleared when window closes (close() clears first)
        self._port_poll_timer.stop()
        self.port.close()
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
            config_store.ui_settings(self.config)['start_hidden'] = False
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
        dlg = SettingsPopup(config_store.ui_settings(self.config), getattr(sys, 'frozen', False), parent=self)
        if dlg.exec() == QDialog.Accepted:
            res = dlg.get_result()
            try:
                ui = config_store.ui_settings(self.config)
                mode = (res.get('start_mode') or 'normal').lower()
                if mode not in ('normal', 'minimized', 'hidden'):
                    mode = 'normal'
                # Always persist start_mode; manual launches ignore it (applied only if autostart)
                ui['start_mode'] = mode
                ui['minimize_to_tray'] = bool(res.get('minimize_to_tray'))
                ui['autostart_enabled'] = bool(res.get('autostart_enabled'))
                ui['start_hidden'] = (mode == 'hidden') and bool(res.get('autostart_enabled'))
                # Update Windows autostart
                if sys.platform == 'win32':
                    ok = self._update_autostart_registry(ui['autostart_enabled'])
                    if not ok and ui['autostart_enabled']:
                        show_message(self, 'Autostart', 'Failed to update Windows autostart registry.')
                self.save_config(silent=True)
                # Apply mode immediately
                if not ui['autostart_enabled']:
                    # Manual run always shown normal regardless of chosen mode when autostart is off
                    self.showNormal()
                    self.raise_()
                    self.activateWindow()
                elif mode == 'normal':
                    self.showNormal()
                    self.raise_()
                    self.activateWindow()
                elif mode == 'minimized':
                    self.show()
                    self.showMinimized()
                elif mode == 'hidden':
                    self.hide()
                self.statusBar().showMessage('Settings saved.', 4000)
            except Exception as e:
                show_message(self, 'Settings', f'Failed to save settings: {e}')

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
                        # pythonw.exe: no console window at login
                        if (py.parent / 'pythonw.exe').exists():
                            py = py.parent / 'pythonw.exe'
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

    # ---- Presets System (menu-based) ----
    def _rebuild_presets_menu(self):
        self.presets_menu.clear()
        presets = self.store.list_presets()
        if presets:
            for name in presets:
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
        safe = config_store.sanitize_name(name)
        if not safe:
            show_message(self, "Preset", "Invalid name.")
            return
        if self.store.has_preset(safe):
            show_message(self, "Preset", "Preset already exists.")
            return
        # Start from blank current layer (preserving other layers)
        # Keep navigation pads if already present, otherwise seed them; clear all other pads for this layer only
        old = self.layers[self.active_layer].get("pads", {})
        self.layers[self.active_layer]["pads"] = {k: old.get(k, v) for k, v in config_store.default_nav_pads().items()}
        self.update_grid()
        self.current_preset_name = safe
        if self._write_current_preset():
            self.statusBar().showMessage(f"Created preset '{safe}'", 4000)

    def _write_current_preset(self) -> bool:
        """Save the active layer's pads as preset current_preset_name; warn and return False on failure."""
        pads = self.layer_set.active_pads
        try:
            self.store.write_preset(self.current_preset_name, self.active_layer, pads, self.animations)
        except Exception as e:
            show_message(self, "Preset", f"Failed to save preset: {e}")
            return False
        return True

    def save_current_preset(self):
        if not self.current_preset_name:
            self.save_current_preset_as()
            return
        if self._write_current_preset():
            self.statusBar().showMessage(f"Saved layer preset '{self.current_preset_name}'", 4000)

    def save_current_preset_as(self):
        name, ok = QInputDialog.getText(self, "Save Preset As", "Preset name:")
        if not ok or not name.strip():
            return
        safe = config_store.sanitize_name(name)
        if not safe:
            show_message(self, "Preset", "Invalid name.")
            return
        self.current_preset_name = safe
        self.save_current_preset()

    def delete_current_preset(self):
        if not self.current_preset_name:
            show_message(self, "Preset", "No preset loaded.")
            return
        # Enforce at least one preset existing
        if len(self.store.list_presets()) <= 1:
            show_message(self, "Preset", "Cannot delete the last remaining preset.")
            return
        if not ask_yes_no(self, "Delete Layer Preset", f"Delete preset '{self.current_preset_name}'?"):
            return
        try:
            self.store.delete_preset(self.current_preset_name)
        except Exception as e:
            show_message(self, "Preset", f"Could not delete: {e}")
            return
        deleted = self.current_preset_name
        self.current_preset_name = None
        remaining = self.store.list_presets()
        self.statusBar().showMessage(f"Layer preset '{deleted}' deleted", 4000)
        if remaining:
            prevs = [n for n in remaining if n < deleted]
            self.load_preset(prevs[-1] if prevs else remaining[0])
        else:
            self._apply_config(config_store.default_config(), dirty=False)

    def import_preset_file(self):
        fname, _ = QFileDialog.getOpenFileName(
            self, "Import Preset", str(self.store.presets_dir), "YAML Files (*.yaml)"
        )
        if not fname or not Path(fname).exists():
            return
        try:
            name = self.store.import_preset(fname)
        except Exception as e:
            show_message(self, "Import", str(e))
            return
        self.load_preset(name)
        self.statusBar().showMessage(f"Imported layer preset '{name}'", 4000)

    def load_preset(self, name: str):
        """Load a layer-scoped preset: replace only current layer's pads.

        The store rejects names that resolve outside the presets dir (path traversal).
        """
        try:
            preset = self.store.read_preset(name, current_layer=self.active_layer)
        except UnsafePresetName as e:
            show_message(self, "Security Error", str(e))
            return
        except PresetError as e:
            show_message(self, "Preset", str(e))
            return
        self._stop_animation_playback()
        # Merge embedded animations (if present) into global animations set
        self.animations.update(preset.animations)
        # Preserve navigation buttons if the preset lacks them
        old = self.layers[self.active_layer]["pads"]
        pads = {**preset.pads}
        for key, nav in config_store.default_nav_pads().items():
            pads.setdefault(key, old.get(key, nav))
        self.layers[self.active_layer]["pads"] = pads
        self.current_preset_name = name
        # Persist last_preset in config ui section for auto-load on next start
        config_store.ui_settings(self.config)['last_preset'] = name
        self.save_config(silent=True)
        # Repaints every pad, so no lights linger from the previous preset
        self.update_grid()
        self.statusBar().showMessage(f"Loaded layer preset '{name}'", 4000)

    # load_selected_preset deprecated (use load_preset via menu)

    # --- Pad press callbacks ---
    def on_pad_press(self, pad):
        x, y = pad
        mapping = PadMapping.from_dict(self.get_pad_mapping(pad))
        # Any press ends animation lights held 'until press'
        if self.anim.release_press_holds():
            self._animate()
        # Layer changes repaint and move the list selection via _on_layers_changed
        if mapping.type == padmap.LAYER_NAV:
            # Anything but "prev" goes to the next layer (wraps around)
            if mapping.nav == "prev":
                self.layer_set.prev()
            else:
                self.layer_set.next()
        elif mapping.type == padmap.SWITCH_LAYER:
            target = mapping.layer
            if not self.layer_set.switch_to(target):
                self.statusBar().showMessage(f"Layer '{target}' not found", 4000)
        elif mapping.type == padmap.ANIMATION:
            anim_name = mapping.name
            if anim_name and anim_name in self.animations:
                # 0=infinite (a press while it loops stops it), 1=single cycle
                self._play_animation(anim_name, mapping.iterations, mapping.hold)
        else:
            result = self.action_runner.run(mapping)
            if not result.ok:
                self._report_action_error(result.error)
        self.animate_pad_press(x, y)

    def _report_action_error(self, message: str):
        # No modal dialog: the app often lives in the tray, and a nested event loop let presses re-enter
        self.statusBar().showMessage(message, 5000)
        tray = getattr(self, 'tray', None)
        if tray is not None and not self.isVisible():
            tray.showMessage("Launchpad Mapper", message, QSystemTrayIcon.Warning, 5000)

    def on_pad_release(self, pad):
        self.animate_pad_release(pad[0], pad[1])

    @Slot(int, int)
    def _on_midi_pressed(self, x, y):
        self.on_pad_press((x, y))

    @Slot(int, int)
    def _on_midi_released(self, x, y):
        self.on_pad_release((x, y))

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
        dlg = PadEditorDialog(
            pad,
            mapping,
            self,
            animation_names=sorted(self.animations.keys()),
            last_color=self._last_color,
            last_intensity=self._last_intensity,
        )
        accepted = dlg.exec() == QDialog.Accepted
        self._sync_custom_colors()
        if accepted:
            if dlg.picked:
                self._last_color, self._last_intensity = dlg.picked
            pads = self.layer_set.active_pads
            new_map = dlg.result_mapping()
            if new_map is None:
                pads.pop(pad_key(pad), None)
            else:
                pads[pad_key(pad)] = new_map
            self.update_grid()
            if self._ready:
                self.mark_dirty()

    def update_grid(self):
        """Show the active layer's colors (intensity applied) on screen and repaint every Launchpad pad."""
        pads = self.layer_set.active_pads
        base = padmap.pad_colors(pads)
        for pad, btn in self.pad_buttons.items():
            btn.update_color(padmap.screen_hex(pads.get(pad_key(pad))))
        # Full repaint (each pad once, no clear first: clearing then repainting made the pads flicker)
        self.leds.paint(base=base, full=True)

    def _sync_layer_list(self):
        """Mirror the layer set in layer_list (rebuilt only if names changed) without re-entering switch_layer."""
        names = self.layer_set.names
        self.layer_list.blockSignals(True)
        try:
            if [self.layer_list.item(i).text() for i in range(self.layer_list.count())] != names:
                self.layer_list.clear()
                self.layer_list.addItems(names)
            self.layer_list.setCurrentRow(names.index(self.layer_set.active))
        finally:
            self.layer_list.blockSignals(False)

    def _on_layers_changed(self):
        self._sync_layer_list()
        self.update_grid()

    def switch_layer(self, name):
        """User picked a row in layer_list."""
        self.layer_set.switch_to(name)

    def save_config(self, silent: bool = False):
        """Persist full configuration (layers + animations)."""
        self.config["active_layer"] = self.layer_set.active
        self.config["layers"] = self.layer_set.layers
        self.config["animations"] = self.animations
        # Ensure UI section exists; keep custom fields
        try:
            ui = config_store.ui_settings(self.config)
            # Maintain start_mode preference; derive start_hidden for backward compatibility
            ui.setdefault('start_mode', 'normal')
            # isHidden() is True during construction, before the window is first shown
            if ui.get('start_mode') == 'hidden' or (self._ready and self.isHidden()):
                ui['start_hidden'] = True
            else:
                ui['start_hidden'] = False
            # Preserve existing flags if present
            ui.setdefault('minimize_to_tray', ui.get('minimize_to_tray', False))
            ui.setdefault('autostart_enabled', ui.get('autostart_enabled', False))
            # last_preset may already be set elsewhere
            ui.setdefault('last_preset', ui.get('last_preset'))
        except Exception:
            pass
        try:
            self.store.save(self.config)
            self._dirty = False
            if not silent:
                show_message(self, "Saved", f"Config saved to {self.store.config_path}")
        except Exception as e:
            if not silent:
                show_message(self, "Save", f"Failed to save config: {e}")
            else:
                self.statusBar().showMessage(f"Autosave failed: {e}", 8000)

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
                painter.fillRect(option.rect, QColor(255, 255, 255, 28))
            # Triangle points
            cx = r.center().x()
            cy = r.center().y()
            if element == QStyle.PE_IndicatorSpinUp:
                pts = [QPoint(cx, cy - 5), QPoint(cx - 5, cy + 3), QPoint(cx + 5, cy + 3)]
            elif element == QStyle.PE_IndicatorSpinDown:
                pts = [QPoint(cx, cy + 5), QPoint(cx - 5, cy - 3), QPoint(cx + 5, cy - 3)]
            else:  # combobox
                pts = [QPoint(cx, cy + 5), QPoint(cx - 6, cy - 2), QPoint(cx + 6, cy - 2)]
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(255, 255, 255), 1))
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
        ui = config_store.ui_settings(ConfigStore(APPDATA_DIR).load().config)
        saved_mode = ui.get('start_mode') or ('hidden' if ui.get('start_hidden') else 'normal')
    except Exception:
        pass
    if cli_hidden:
        effective_mode = 'hidden'
    elif is_autostart:
        effective_mode = saved_mode if saved_mode in ('normal', 'minimized', 'hidden') else 'normal'
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
                del sys.argv[idx : idx + 2]
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
                        win.showNormal()
                        win.raise_()
                        win.activateWindow()
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
                    win._single_server = server
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
            if win.store.has_preset(preset_to_load):
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
            if w and getattr(w, 'port', None):
                try:
                    # Clears LEDs, then closes; a no-op if closeEvent already did it
                    w.port.close()
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


if __name__ == "__main__":
    main()
