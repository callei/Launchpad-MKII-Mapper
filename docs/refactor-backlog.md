# Refactor backlog

Duplicated and dead code deliberately left in place during the bug-fix pass (2026-11).
Work through it with `/improve-codebase-architecture`; until then, don't refactor these as part of unrelated changes.
Line numbers drift, so search by name.

## Duplicated
- **Default nav pads** (`"2,-1"` prev `#2980b9`, `"3,-1"` next `#8e44ad`) written out in `MainWindow.__init__`, `create_initial_preset_if_missing`, `add_layer_dialog`, `new_preset_dialog`, `load_preset`, `_apply_default_config`.
- **Legacy type map** (`layer_nav`/`switch_layer`/`run_process` -> spaced names): `_normalize_all_mappings` and `edit_pad` in `gui/launchpad_mapper.py`; `PadEditorDialog.__init__` and `_on_type_changed` in `gui/widgets.py`.
- **Hex color + intensity -> RGB**: `_get_base_color`, `_anim_scheduler_tick`, `_compute_base_colors`.
- **Config loading**, four variants that drift apart: `_load_config_on_startup`, `load_config` (unused), `load_config_dialog`, `import_config`.
- **Layer list rebuild** (`layer_list.clear()` + `addItems(...)`): `import_config`, `delete_current_layer`, `_apply_default_config`, `load_config_dialog`.
- **`ui` config section access** (`self.config.get('ui', {}) if isinstance(...)`) repeated in about 10 methods.

## Dead
- `_compute_base_colors`, `_apply_base_colors_to_hardware`: never called; read `_animation_scope` / `_animation_base_colors`, which are never set.
- `_play_animation_frame`: empty, "deprecated".
- `validate_pad_mapping`: never called.
- `load_config`: never called.

## Structural
- `_perform_hotkey` / `_perform_media_control` are module-level functions monkey-patched onto `MainWindow`.
- `MainWindow` (~2,200 lines) holds UI, persistence, presets, animations, tray, autostart and action execution in one class.
