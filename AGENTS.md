# Launchpad MKII Mapper

Windows desktop app (Python 3.11, PySide6) that maps Novation Launchpad MKII pads to actions, colors and animations.

## Run
- `python gui/launchpad_mapper.py` from the repo root (use `.venv`). `gui/` is the script dir, so `from widgets import ...` only resolves when run this way; `launchpad/` is reached by inserting the project root into `sys.path`.
- Flags: `--debug` writes `%APPDATA%/LaunchpadMapper/startup.log`; `--preset <name>`; `--hidden`.
- Single-instance guard (QLocalServer): a second launch hands off to the running instance instead of starting. Quit the tray app before re-running.
- Install deps from `requirements.txt` (includes `pywin32`, which `pyproject.toml` lacks).

## Verifying changes
- `.venv\Scripts\python.exe -m pytest` (single test: `-m pytest tests/test_controller.py::test_mk2_known_notes`). Tests cover only `launchpad/controller.py` pure logic, without hardware.
- Lint/format: `.venv\Scripts\ruff.exe check .` and `ruff format .` (config in `pyproject.toml`). A PostToolUse hook (`.claude/hooks/ruff_hook.py`) formats each edited `.py` file and reports lint errors.
- GUI and MIDI behavior is verified by running from source with a real Launchpad MKII connected.
- Without hardware the controller silently falls back to virtual mode (no input, LED writes dropped), so GUI-only checks don't prove MIDI/LED behavior. Say so when you couldn't test on hardware.

## Architecture
- `launchpad/controller.py`: `LaunchpadController`, low-level mido/rtmidi I/O. Converts MIDI notes <-> pad coords and sends LED colors as MKII SysEx (RGB 0-63, with per-channel calibration).
- `gui/launchpad_mapper.py`: `MainWindow`, which holds all app state (layers, pads, animations, presets, tray, autostart registry, action execution). `_perform_hotkey` / `_perform_media_control` are defined at module bottom and monkey-patched onto `MainWindow` (Win32 `user32` via ctypes).
- `gui/widgets.py`: popups/dialogs (pad editor, color picker, settings, app picker).
- Pad coordinates: `(x, y)`, with grid 0-7 and y=0 the top row; the top round buttons are `y = -1`; the right column is `x = 8`. In YAML, pads are keyed as the string `"x,y"`.
- Mapping `type` values use spaces: `"layer nav"`, `"launch app"`, `"run process"`, `"hotkey"`, `"media control"`, `"animation"`, `"color"`. `_normalize_all_mappings` rewrites legacy underscore forms (`layer_nav` ...) on load, so keep that mapping when renaming types.

## Gotchas
- Pad callbacks (`on_pad_press`/`on_pad_release`) run on the rtmidi thread. Route any new Qt/UI work through a `Signal` (see `start_animation_requested`, `pad_pressed`) instead of touching widgets directly.
- User config and presets live in `%APPDATA%/LaunchpadMapper/` (`config.yaml`, `presets/`). Repo `presets/*.yaml` are copied there once if missing. Repo `config.yaml` is a sample only; the app never reads it.
- Running actions is security-sensitive: keep `subprocess` calls `shell=False`, and keep `_is_safe_path` checks on preset/file paths.
- Windows-only features (`winreg`, ctypes `user32`) are guarded by `sys.platform == 'win32'`. Keep the guards.

## Known debt
- Duplicated and dead code is listed in `docs/refactor-backlog.md`. Leave it alone in unrelated changes; it's cleaned up in a dedicated refactor pass.

## Repo etiquette
- Solo project: commit directly to `main`.
- Don't touch release packaging (`build_exe.ps1`, `installer.iss`, `dist/`, `build/`, `Output/`). They are local, untracked and owner-managed.
