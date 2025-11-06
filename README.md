# Launchpad Mapper

A Windows desktop app (PySide6) for mapping Novation Launchpad pads to actions and lighting them with colors and animations. It supports layers, per-pad actions (launch app, run process, hotkeys, media controls), presets, and simple pad animations.

## Features
- 8x8 grid + control pads UI with live color preview
- Layers with quick navigation pads
- Actions per pad: color only, run process, launch app, hotkey sequences, media controls, switch layer, start animation
- Presets: save/load layer mappings as YAML
- Simple keyframe animations (per-pad colors, durations, loop)
- Settings gear (top bar) with:
	- Startup mode: normal, minimized, or hidden (tray only)
	- Close to tray (keep running)
	- Autostart with Windows (HKCU Run)
- Auto-loads your last used layer preset (falls back to `empty` if present)
- System tray icon with quick actions (Open, Reconnect, Settings, Quit)
- Works without Launchpad connected (offline UI); virtual mode fallback if MIDI ports are busy

## Install (Users)
Download the latest installer from GitHub Releases and run it.
- App installs under Program Files
- User data under `%APPDATA%/LaunchpadMapper` (no admin needed for presets)
- Uninstall removes `%APPDATA%/LaunchpadMapper` (config + presets)

Startup and autostart:
- Control startup behavior in-app via the Settings gear (normal, minimized, hidden)
- Toggle “Start with Windows” in Settings (uses HKCU\Run; no extra flags)

Command-line (for development/testing):
- `--hidden` (or legacy `--background`) to start hidden in tray
- `--preset <name>` to load a preset after start
- `--debug` to write a startup log to `%APPDATA%/LaunchpadMapper/startup.log`

## Presets & Config
- Config: `%APPDATA%/LaunchpadMapper/config.yaml`
- Presets: `%APPDATA%/LaunchpadMapper/presets/*.yaml`
- On first run, default presets shipped with the app are copied to AppData once

## Build (Developers)
Prereqs: Python 3.11, a venv, Inno Setup 6 for installer.

Create/refresh the EXE:
```powershell
.\build_exe.ps1
# or
.\build_exe.ps1 -Clean
```

Run from source (PowerShell on Windows):
```powershell
.\.venv\Scripts\python.exe .\gui\launchpad_mapper.py
# Hidden (tray) – prefer the Settings gear in-app for persistent behavior
.\.venv\Scripts\python.exe .\gui\launchpad_mapper.py --hidden
# With preset
.\.venv\Scripts\python.exe .\gui\launchpad_mapper.py --preset empty
```

Build installer (requires Inno Setup 6):
```powershell
"C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe" .\installer.iss
```

## Packaging Notes
- PyInstaller onedir build with windowed bootloader (no console)
- Hidden imports for `win32com.client` (pywin32) and MIDI backend handled in `build_exe.ps1`
- Fonts and icons are bundled; custom font applied at runtime

## Troubleshooting
- Launchpad not found / device busy: The app auto-falls back to a virtual mode; you can still edit mappings and animations.
- MIDI backend: ensure `mido` and `python-rtmidi` are installed in your venv; packaged builds bundle `_rtmidi`.
- Installed apps list empty in App Picker: install `pywin32` (win32com). Packaged builds include it.
- Debug startup: run with `--debug` (or set env `LAUNCHPADMAPPER_DEBUG=1`) and inspect `%APPDATA%/LaunchpadMapper/startup.log`.

## License
See `LICENSE`.
