# Launchpad Mapper

A Windows desktop app (PySide6) for mapping Novation Launchpad pads to actions and lighting them with colors and animations. It supports layers, per-pad actions (launch app, run process, hotkeys, media controls), presets, and simple pad animations.

## Features
- 8x8 grid + control pads UI with live color preview
- Layers with quick navigation pads
- Actions per pad: color only, run process, launch app, hotkey sequences, media controls, switch layer, start animation
- Presets: save/load layer mappings as YAML
- Simple keyframe animations (per-pad colors, durations, loop)
- System tray with background start (`--background` / `--hidden`)
- Remembers last visibility (opens hidden if you closed it hidden)
- Works without Launchpad connected (offline UI)

## Install (Users)
Download the latest installer from GitHub Releases and run it.
- App installs under Program Files
- User data under `%APPDATA%/LaunchpadMapper` (no admin needed for presets)
- Uninstall removes `%APPDATA%/LaunchpadMapper` (config + presets)

Command-line options:
- `--background` or `--hidden` to start in tray
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

Run from source:
```powershell
.\.venv\Scripts\python.exe .\gui\launchpad_mapper.py
# Background
.\.venv\Scripts\python.exe .\gui\launchpad_mapper.py --background
# With preset
.\.venv\Scripts\python.exe .\gui\launchpad_mapper.py --preset empty
```

Build installer (uses version from pyproject.toml):
```powershell
.\build_installer.ps1
# Explicit version override
.\build_installer.ps1 -VersionOverride 0.1.2
```
Or directly with Inno Setup:
```powershell
"C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe" .\installer.iss
```

## Packaging Notes
- PyInstaller onedir build with windowed bootloader (no console)
- Hidden imports for mido/python-rtmidi backend are handled in build script
- Fonts and icons are bundled, custom font is applied at runtime

## Troubleshooting
- Launchpad not found: The UI still runs; connect and press “Connect Launchpad”
- MIDI backend errors: ensure `mido` and `python-rtmidi` are installed; our build bundles the native `_rtmidi` binary.
- Debug startup: run with `--debug` and inspect `%APPDATA%/LaunchpadMapper/startup.log`

## License
See `LICENSE`.
