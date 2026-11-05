import json

import pytest
import yaml

from launchpad import config_store
from launchpad.config_store import ConfigStore, PresetError, UnsafePresetName


@pytest.fixture
def store(tmp_path):
    s = ConfigStore(tmp_path / "app")
    s.ensure_presets()
    return s


def _cfg(**layers):
    return {"active_layer": next(iter(layers)), "layers": layers, "animations": {}}


def test_missing_config_loads_defaults(store):
    result = store.load()
    assert not result.loaded and result.problems == []
    assert result.config == config_store.default_config()


def test_save_load_round_trip_keeps_layer_order(store):
    cfg = _cfg(zeta={"pads": {"0,0": {"type": "color", "color": "#ff0000"}}}, alpha={"pads": {}})
    cfg["ui"] = {"start_mode": "hidden"}
    store.save(cfg)
    result = store.load()
    assert result.loaded and result.problems == []
    assert list(result.config["layers"]) == ["zeta", "alpha"]
    assert result.config["layers"]["zeta"]["pads"]["0,0"]["color"] == "#ff0000"
    assert config_store.ui_settings(result.config)["start_mode"] == "hidden"


def test_save_keeps_previous_file_as_bak(store):
    store.save(_cfg(first={"pads": {}}))
    store.save(_cfg(second={"pads": {}}))
    bak = yaml.safe_load(store.config_path.with_name("config.yaml.bak").read_text(encoding="utf-8"))
    assert list(bak["layers"]) == ["first"]
    assert list(store.load().config["layers"]) == ["second"]


def test_failed_write_leaves_previous_file_intact(store, monkeypatch):
    store.save(_cfg(good={"pads": {}}))
    before = store.config_path.read_text(encoding="utf-8")

    def broken_dump(data, stream, **kwargs):
        stream.write("active_layer: half")
        raise OSError("disk full")

    monkeypatch.setattr(config_store.yaml, "dump", broken_dump)
    with pytest.raises(OSError):
        store.save(_cfg(bad={"pads": {}}))
    assert store.config_path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in store.config_path.parent.iterdir()) == ["config.yaml", "presets"]  # no temp left


@pytest.mark.parametrize("text", ["layers: [unclosed", "- just\n- a list\n"])
def test_unreadable_config_is_copied_aside_and_defaults_used(store, text):
    store.config_path.write_text(text, encoding="utf-8")
    result = store.load()
    assert not result.loaded and result.problems
    assert result.config == config_store.default_config()
    assert store.config_path.with_name("config.yaml.corrupt").read_text(encoding="utf-8") == text
    assert store.config_path.read_text(encoding="utf-8") == text


def test_load_fills_defaults_and_repairs_active_layer(store):
    store.config_path.write_text(yaml.dump({"active_layer": "", "layers": {"main": {}}}), encoding="utf-8")
    cfg = store.load().config
    assert cfg["active_layer"] == "main"
    assert cfg["layers"]["main"] == {"pads": {}}
    assert cfg["animations"] == {}


def test_load_normalizes_legacy_type_names(store):
    pads = {
        "0,0": {"type": "run_process", "command": "x"},
        "1,0": {"type": "layer_nav", "nav": "next"},
        "2,0": {"type": "switch_layer", "layer": "b"},
        "3,0": {"type": "color only", "color": "#fff"},
    }
    store.config_path.write_text(yaml.dump(_cfg(main={"pads": pads})), encoding="utf-8")
    result = store.load()
    types = [m["type"] for m in result.config["layers"]["main"]["pads"].values()]
    assert types == ["run process", "layer nav", "switch layer", "color"]


def test_load_external_json_file(store, tmp_path):
    src = tmp_path / "cfg.json"
    src.write_text(json.dumps(_cfg(main={"pads": {"0,0": {"type": "run_process"}}})), encoding="utf-8")
    result = store.load(src)
    assert result.loaded
    assert result.config["layers"]["main"]["pads"]["0,0"]["type"] == "run process"


def test_unreadable_external_file_is_reported_not_copied(store, tmp_path):
    src = tmp_path / "bad.yaml"
    src.write_text("layers: [unclosed", encoding="utf-8")
    result = store.load(src)
    assert not result.loaded and result.problems
    assert not (tmp_path / "bad.yaml.corrupt").exists()


def test_ui_settings_creates_and_repairs_section():
    cfg = {"ui": "garbage"}
    ui = config_store.ui_settings(cfg)
    ui["minimize_to_tray"] = True
    assert cfg["ui"] == {"minimize_to_tray": True}
    assert config_store.ui_settings(cfg) is ui


def test_default_nav_pads_are_fresh_copies():
    a = config_store.default_nav_pads()
    a["2,-1"]["color"] = "#000000"
    assert config_store.default_nav_pads()["2,-1"]["color"] == "#2980b9"
    assert config_store.default_config()["layers"]["main"]["pads"] == config_store.default_nav_pads()


def test_ensure_presets_seeds_once_and_creates_empty(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "drums.yaml").write_text(yaml.dump({"layer": "main", "pads": {}}), encoding="utf-8")
    store = ConfigStore(tmp_path / "app", seed_dirs=[seed])
    store.ensure_presets()
    assert store.list_presets() == ["drums", "empty"]
    store.delete_preset("drums")
    store.ensure_presets()  # user deletions stick
    assert store.list_presets() == ["empty"]
    assert store.read_preset("empty").pads == config_store.default_nav_pads()


def test_preset_write_read_embeds_only_referenced_animations(store):
    pads = {"0,0": {"type": "animation", "name": "blink"}, "1,0": {"type": "color", "color": "#fff"}}
    anims = {"blink": {"frames": []}, "unused": {"frames": []}}
    store.write_preset("live", "main", pads, anims)
    preset = store.read_preset("live")
    assert preset.pads == pads
    assert preset.animations == {"blink": {"frames": []}}
    assert store.has_preset("live")
    store.delete_preset("live")
    assert not store.has_preset("live")


def test_read_preset_normalizes_legacy_types(store):
    (store.presets_dir / "old.yaml").write_text(
        yaml.dump({"layer": "main", "pads": {"2,-1": {"type": "layer_nav", "nav": "prev"}}}), encoding="utf-8"
    )
    assert store.read_preset("old").pads["2,-1"]["type"] == "layer nav"


def test_read_old_full_config_style_preset(store):
    old = {
        "active_layer": "b",
        "layers": {"a": {"pads": {"0,0": {"type": "color"}}}, "b": {"pads": {"1,1": {"type": "hotkey"}}}},
        "animations": {"ignored": {}},
    }
    (store.presets_dir / "full.yaml").write_text(yaml.dump(old, sort_keys=False), encoding="utf-8")
    preset = store.read_preset("full", current_layer="a")
    assert preset.pads == {"1,1": {"type": "hotkey"}}
    assert preset.animations == {}
    del old["active_layer"]
    (store.presets_dir / "full.yaml").write_text(yaml.dump(old, sort_keys=False), encoding="utf-8")
    assert store.read_preset("full", current_layer="a").pads == {"0,0": {"type": "color"}}


def test_missing_and_malformed_presets_raise(store):
    with pytest.raises(PresetError):
        store.read_preset("nope")
    (store.presets_dir / "list.yaml").write_text("- a\n", encoding="utf-8")
    with pytest.raises(PresetError):
        store.read_preset("list")


@pytest.mark.parametrize("name", ["../config", "..\\config", "../../evil", "", "/abs"])
def test_path_traversal_in_preset_names_rejected(store, name):
    store.save(config_store.default_config())
    for op in (
        lambda: store.read_preset(name),
        lambda: store.write_preset(name, "main", {}, {}),
        lambda: store.delete_preset(name),
    ):
        with pytest.raises(UnsafePresetName):
            op()
    assert not store.has_preset(name)
    assert store.config_path.exists()


def test_import_preset_copies_without_overwriting(store, tmp_path):
    src = tmp_path / "My Set!.yaml"
    src.write_text(yaml.dump({"layer": "main", "pads": {"0,0": {"type": "color"}}}), encoding="utf-8")
    assert store.import_preset(src) == "MySet"
    assert store.import_preset(src) == "MySet_1"
    assert store.read_preset("MySet_1").pads == {"0,0": {"type": "color"}}


def test_import_preset_rejects_unreadable_file(store, tmp_path):
    src = tmp_path / "bad.yaml"
    src.write_text("pads: [unclosed", encoding="utf-8")
    with pytest.raises(PresetError):
        store.import_preset(src)
    assert store.list_presets() == ["empty"]


def test_export_and_read_mapping(tmp_path):
    data = {"b": {"frames": []}, "a": {"frames": []}}
    for name in ("anims.yaml", "anims.json"):
        config_store.export_file(data, tmp_path / name)
        assert list(config_store.read_mapping(tmp_path / name)) == ["b", "a"]
    (tmp_path / "list.yaml").write_text("- a\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config_store.read_mapping(tmp_path / "list.yaml")


def test_custom_colors_are_16_valid_slots():
    cfg = {"ui": {"custom_colors": ["#FF0000", "nope", 3]}}
    colors = config_store.custom_colors(cfg)
    assert len(colors) == 16
    assert colors[:3] == ["#ff0000", "#ffffff", "#ffffff"]
    assert config_store.custom_colors({}) == ["#ffffff"] * 16
