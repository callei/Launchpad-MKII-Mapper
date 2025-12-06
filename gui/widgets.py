import sys
import os
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QPushButton, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QHBoxLayout, 
    QFileDialog, QDialog, QComboBox, QLineEdit, QColorDialog, QDialogButtonBox, 
    QInputDialog, QSpinBox, QCheckBox
)
from PySide6.QtCore import Qt, Signal, QPoint, QTimer, QThread
from PySide6.QtGui import QColor, QPainterPath, QRegion

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


class ColorPickerPopup(FramelessPopup):
    """Custom color picker wrapped in our frameless popup to maintain consistent styling.

    Now also includes an intensity selector (low/medium/high) so intensity travels with color
    selections across both mapping and animation editors.
    """
    def __init__(self, initial_color: str | None, original_color: str | None, parent=None, initial_intensity: str | None = None):
        super().__init__(title="Pick Color", parent=parent)
        self._selected: str | None = initial_color
        self._original = original_color
        self._intensity = (initial_intensity or "medium").lower()
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
        # Intensity selector + buttons row
        inten_row = QHBoxLayout()
        inten_row.addWidget(QLabel("Intensity:"))
        self.intensity_select = QComboBox()
        self.intensity_select.addItems(["low", "medium", "high"])
        try:
            idx = ["low","medium","high"].index(self._intensity)
        except ValueError:
            idx = 1
        self.intensity_select.setCurrentIndex(idx)
        inten_row.addWidget(self.intensity_select)
        lay.addLayout(inten_row)
        # Extra buttons row
        btn_row = QHBoxLayout()
        self.clear_btn = QPushButton("Clear")
        self.store_current_btn = QPushButton("Store Color → Slot…")
        # Custom clear OK/Cancel buttons (built-in ones are no longer removed/moved dynamically)
        self.ok_btn = QPushButton("OK")
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.clear_btn)
        btn_row.addWidget(self.store_current_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)
        self._btn_row = btn_row  # kept for backward compatibility (if we later want to reuse relocation)
        self.clear_btn.clicked.connect(self._clear_color)
        self.store_current_btn.clicked.connect(self._store_current)
        self.ok_btn.clicked.connect(self._accept)
        self.cancel_btn.clicked.connect(self.reject)
        # For safety: catch double clicks in the color box etc
        self._dlg.accepted.connect(self._accept)
        self._dlg.rejected.connect(self.reject)
        # Remove &-mnemonics
        QTimer.singleShot(0, self._strip_mnemonics)
        # No dynamic moving of internal buttons anymore (custom buttons are used)
        QTimer.singleShot(0, self._hide_internal_dialog_buttons)

    def _hide_internal_dialog_buttons(self):
        """Hides built-in OK/Cancel in QColorDialog so only our own are visible."""
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
        self._intensity = (self.intensity_select.currentText() or "medium").lower()
        self.accept()

    def get_selected_color(self) -> str | None:
        return self._selected
    def get_selected_intensity(self) -> str:
        return (self._intensity or "medium").lower()


class SettingsPopup(FramelessPopup):
    """Settings dialog for startup behavior and tray options."""
    def __init__(self, ui_cfg: dict, is_frozen: bool, parent=None):
        super().__init__(title="Settings", parent=parent)
        self.set_popup_title("Settings")
        self._ui_cfg = dict(ui_cfg) if isinstance(ui_cfg, dict) else {}
        lay = self.content_layout()

        lay.addWidget(QLabel("When starting the app (applies to autostart only):"))
        self.start_mode = QComboBox()
        self.start_mode.addItems(["normal", "minimized", "hidden"])  # hidden = tray only
        current_mode = (self._ui_cfg.get("start_mode") or ("hidden" if self._ui_cfg.get("start_hidden") else "normal")).lower()
        if current_mode not in ("normal","minimized","hidden"):
            current_mode = "normal"
        self.start_mode.setCurrentText(current_mode)
        lay.addWidget(self.start_mode)

        self.minimize_to_tray_cb = QCheckBox("Close button hides to system tray (keep running)")
        self.minimize_to_tray_cb.setChecked(bool(self._ui_cfg.get("minimize_to_tray", False)))
        lay.addWidget(self.minimize_to_tray_cb)

        self.autostart_cb = QCheckBox("Start with Windows")
        self.autostart_cb.setChecked(bool(self._ui_cfg.get("autostart_enabled", False)))
        lay.addWidget(self.autostart_cb)
        if not is_frozen:
            self.autostart_cb.setEnabled(True)
            self.autostart_cb.setToolTip("Requires installed app to reliably start with Windows.")
        # Enable start_mode only when autostart is checked
        self.start_mode.setEnabled(self.autostart_cb.isChecked())
        self.autostart_cb.toggled.connect(self.start_mode.setEnabled)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        lay.addWidget(btns)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

    def get_result(self):
        return {
            "start_mode": self.start_mode.currentText(),
            "minimize_to_tray": self.minimize_to_tray_cb.isChecked(),
            "autostart_enabled": self.autostart_cb.isChecked(),
        }


class TextInputPopup(FramelessPopup):
    """Frameless styled text input box (replaces QInputDialog.getText)."""
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
            entries: list[tuple[str,str,str|None,str,bool]] = []  # (label, fullPath, targetPath, args, is_link)
            seen_effective = set()

            def add_entry(label: str, full: str, target: str | None, args: str, is_link: bool):
                if not label:
                    return
                eff = target or full
                # Basic sanity on path
                if not eff or not (eff.lower().endswith('.exe') or eff.lower().endswith('.lnk')):
                    return
                key = (label.lower(), eff.lower(), (args or '').lower())
                if key in seen_effective:
                    return
                seen_effective.add(key)
                entries.append((label, full, target, args, is_link))

            # --- 1. Start Menu ---
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
            for root in dirs:
                for path in root.rglob('*'):
                    if self._stop:
                        return
                    if not path.is_file():
                        continue
                    suf = path.suffix.lower()
                    if suf not in exts:
                        continue
                    full = str(path)
                    target = None
                    is_link = (suf == '.lnk')
                    if is_link and shell:
                        try:
                            sc = shell.CreateShortcut(full)
                            target = sc.TargetPath or None
                            args = sc.Arguments or ""
                        except Exception:
                            target = None
                            args = ""
                    label = path.stem
                    add_entry(label, full, target, args if is_link else "", is_link)

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
                                            # Heuristic: look for an exe with a similar name in the directory
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
                                                # Take largest exe as fallback
                                                pick = max(candidates, key=lambda p: p.stat().st_size if p.exists() else 0)
                                            if pick:
                                                t = str(pick)
                                            else:
                                                t = ''
                                        if t.lower().endswith('.exe'):
                                            add_entry(name, t, t, "", False)
                                    # else: skip entries without path
                            except Exception:
                                continue
                except Exception:
                    continue

            # --- 3. Fallback: Program Files root (take top-level exes in each subdirectory) if we still have very few ---
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
                            # Find candidates in direct root (not deep) for speed
                            exes = list(subdir.glob('*.exe'))
                            if not exes:
                                continue
                            # Heuristic: choose largest exe
                            pick = max(exes, key=lambda p: p.stat().st_size if p.exists() else 0)
                            label = subdir.name
                            add_entry(label, str(pick), str(pick), "", False)
                            if len(entries) > 60:  # limit fallback volume
                                break
                    except Exception:
                        continue

            # If nothing found despite sources, add common system apps as minimal baseline
            if not entries:
                for sys_app in ["notepad.exe", "calc.exe", "mspaint.exe"]:
                    path = os.path.join(os.environ.get('SystemRoot', 'C:/Windows'), sys_app)
                    if os.path.exists(path):
                        entries.append((sys_app[:-4].title(), path, path, "", False))
            entries.sort(key=lambda t: t[0].lower())
            self.resultReady.emit(entries)
        except Exception:
            self.resultReady.emit([])

class AppPickerDialog(QDialog):
    """List installed programs (cleaned) with Start menu as source.
    Shows only executable .exe (direct or via .lnk) and filters out obvious tools/uninstall shortcuts.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Installed Application")
        self.resize(560, 540)
        v = QVBoxLayout(self)
        top_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter… (type to filter)")
        self.show_all_cb = QCheckBox("Show more")
        top_row.addWidget(self.filter_edit)
        top_row.addWidget(self.show_all_cb)
        v.addLayout(top_row)
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        self.status_lbl = QLabel("Scanning Start menu…")
        v.addWidget(self.status_lbl)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(btns)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        self.filter_edit.textChanged.connect(self._refilter)
        self.show_all_cb.toggled.connect(self._refilter)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        self._raw_entries = []  # list of tuples: (label, full, target, args, is_link)
        self._all_items: list[tuple[str,str,str]] = []  # (label, effective_path, args)
        self._worker = _AppScanWorker(self)
        self._worker.resultReady.connect(self._on_scan_done)
        self._worker.start()
        QTimer.singleShot(0, self.filter_edit.setFocus)

    def _on_scan_done(self, entries):
        self._raw_entries = entries
        self.status_lbl.setText(f"Found {len(entries)} entries. Refining…")
        self._prepare_items()
        self.status_lbl.setText(f"Showing {len(self._all_items)} programs")
        self._refilter()

    def _prepare_items(self):
        cleaned = []
        allowed_sys = {"notepad.exe", "mspaint.exe", "calc.exe"}
        prog_dirs = [p.lower() for p in [os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')] if p]
        for label, full, target, args, is_link in self._raw_entries:
            eff = (target or full)
            if not eff:
                continue
            if not (eff.lower().endswith('.exe') or eff.lower().endswith('.lnk')):
                # Skip non-executable targets
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
            cleaned.append((label, eff, args or ""))
        # Remove dups by effective path keeping first label
        seen = set()
        unique = []
        for label, eff, args in cleaned:
            if (eff, (args or '').lower()) in seen:
                continue
            seen.add((eff, (args or '').lower()))
            unique.append((label, eff, args or ""))
        unique.sort(key=lambda t: t[0].lower())
        self._all_items = unique

    def _refilter(self):
        text = self.filter_edit.text().strip().lower()
        show_all = self.show_all_cb.isChecked()
        items = self._all_items if not show_all else [
            (l, (t or f) if (t or f) else f, (a or "")) for (l, f, t, a, is_link) in self._raw_entries
            if (t or f) and (t or f).lower().endswith('.exe')
        ]
        if text:
            items = [p for p in items if text in p[0].lower() or text in os.path.basename(p[1]).lower()]
        self._rebuild_list(items)
        self.status_lbl.setText(f"Showing {len(items)} programs")

    def _rebuild_list(self, items):
        self.list.clear()
        for label, full, args in items:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, (full, args))
            self.list.addItem(it)

    def get_selected(self) -> tuple[str, str] | None:
        it = self.list.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

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
        self.launch_args_label = QLabel("Arguments:")
        self.launch_args = QLineEdit()
        self.launch_browse_btn = QPushButton("Browse…")
        self.launch_pick_btn = QPushButton("Pick Installed…")
        launch_row = QHBoxLayout()
        launch_row.addWidget(self.launch_path)
        launch_row.addWidget(self.launch_browse_btn)
        launch_row.addWidget(self.launch_pick_btn)
        layout.addWidget(self.launch_label)
        lrw = QWidget(); lrw.setLayout(launch_row)
        layout.addWidget(lrw)
        # Arguments and elevation
        args_row = QHBoxLayout()
        args_row.addWidget(self.launch_args_label)
        args_row.addWidget(self.launch_args, 1)
        args_row_w = QWidget(); args_row_w.setLayout(args_row)
        layout.addWidget(args_row_w)
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
        # Color intensity (handled in color picker; keep hidden storage to persist value)
        self.intensity_label = QLabel("Color intensity:")
        self.intensity_select = QComboBox()
        self.intensity_select.addItems(["low", "medium", "high"])  # default medium
        inten_row = QHBoxLayout()
        inten_row.addWidget(self.intensity_label)
        inten_row.addWidget(self.intensity_select)
        inten_w = QWidget(); inten_w.setLayout(inten_row)
        inten_w.setVisible(False)
        self.intensity_label.setVisible(False)
        self.intensity_select.setVisible(False)
        layout.addWidget(inten_w)
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
            args_row_w,
            self.launch_args_label,
            self.launch_args,
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
            # Intensity default medium
            inten = str(mapping.get("intensity", "medium")).lower()
            if inten not in ("low","medium","high"):
                # numeric legacy 1..3 guarded parsing
                raw_int = mapping.get("intensity")
                n: int | None = None
                if isinstance(raw_int, int):
                    n = raw_int
                elif isinstance(raw_int, str) and raw_int.isdigit():
                    try:
                        n = int(raw_int)
                    except Exception:
                        n = None
                inten = {1:"low",2:"medium",3:"high"}.get(n, "medium") if n in (1,2,3) else "medium"
            self.intensity_select.setCurrentText(inten)
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
            if mtype == "launch app":
                self.launch_args.setText(str(mapping.get("args", "")))
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
        # launch args
        try:
            self.launch_args_label.setVisible(is_launch)
            self.launch_args.setVisible(is_launch)
            self.launch_args.parentWidget().setVisible(is_launch)
        except Exception:
            pass
        # Adjust size so the dialog can shrink when controls hide
        try:
            # Reset minima to allow shrinking
            self.setMinimumSize(0, 0)
            self.setMinimumHeight(0)
            self.resize(self.sizeHint())
            self.adjustSize()
            # For safety run a deferred adjustment (Qt may need layout pass)
            QTimer.singleShot(0, self.adjustSize)
        except Exception:
            pass

    def pick_color(self):
        # Pass current (hidden) intensity into the picker and sync back on accept
        try:
            init_inten = (self.intensity_select.currentText() or "medium").lower()
        except Exception:
            init_inten = "medium"
        picker = ColorPickerPopup(initial_color=self.selected_color, original_color=self.selected_color, parent=self, initial_intensity=init_inten)
        if picker.exec() == QDialog.Accepted:
            self.selected_color = picker.get_selected_color()
            try:
                inten = picker.get_selected_intensity()
                if inten in ("low","medium","high"):
                    self.intensity_select.setCurrentText(inten)
            except Exception:
                pass

    def _browse_launch_app(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Application", str(Path.home()))
        if path:
            self.launch_path.setText(path)
    def _pick_installed_app(self):
        dlg = AppPickerDialog(self)
        if dlg.exec() == QDialog.Accepted:
            sel = dlg.get_selected()
            if sel:
                path, args = sel
                self.launch_path.setText(path)
                if args:
                    self.launch_args.setText(args)
