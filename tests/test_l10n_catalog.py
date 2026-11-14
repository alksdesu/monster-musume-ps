import json

from monmusu_l10n import catalog
from monmusu_l10n.batches import CHARACTERS, ITEMS, packed, waves
from monmusu_l10n.catalog import Entry
from monmusu_l10n.memory import Memory


def written(root, path, document):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def masters(tmp_path):
    written(
        tmp_path,
        "data/shop_data.json",
        {"table": [{"id": 1, "name": "ショップ", "icon": "shop_01", "tags": ["お得"]}]},
    )
    written(
        tmp_path,
        "data/detail_text_data.json",
        {"table": [{"id": "火傷", "text": "火傷の説明"}]},
    )
    written(
        tmp_path,
        "data/attach_ability_data.json",
        {"table": [{"name": "ショップ", "kana": "しょっぷ"}]},
    )
    written(
        tmp_path,
        "unitcard/10001.json",
        {"charaName": "リン", "kana": "りん", "ability": [{"text": "回復"}]},
    )
    written(
        tmp_path,
        "unitcard/10002.json",
        {"charaName": "スズ", "kana": "すず", "ability": [{"text": "回復"}]},
    )
    return tmp_path


def test_text_is_collected_with_where_it_is_shown(tmp_path):
    found = {entry.text: entry.origins for entry in catalog.collect(masters(tmp_path))}
    assert found == {
        "ショップ": ("attach_ability_data:table[].name", "shop_data:table[].name"),
        "お得": ("shop_data:table[].tags[]",),
        "火傷の説明": ("detail_text_data:table[].text",),
        "リン": ("unitcard:charaName",),
        "スズ": ("unitcard:charaName",),
        "回復": ("unitcard:ability[].text",),
    }


def test_readings_and_link_targets_are_left_out(tmp_path):
    texts = {entry.text for entry in catalog.collect(masters(tmp_path))}
    assert not texts & {"しょっぷ", "りん", "すず", "火傷", "shop_01"}


def test_a_catalog_reads_back_as_it_was_saved(tmp_path):
    entries = catalog.collect(masters(tmp_path))
    path = tmp_path / "out" / "source.jsonl"
    catalog.save(entries, path)
    assert catalog.load(path) == entries
    assert b"\r" not in path.read_bytes()


def test_a_name_waits_for_the_shorter_names_inside_it():
    texts = [
        "水着リン",
        "リン",
        "【ヴィーヴル娘】水着リン",
        "短い\n二行",
        "ヴィーヴル娘",
    ]
    grouped = waves(Entry(text, ("unit:name",)) for text in texts)
    assert [[entry.text for entry in wave] for wave in grouped] == [
        ["リン", "ヴィーヴル娘"],
        ["水着リン"],
        ["【ヴィーヴル娘】水着リン"],
        ["短い\n二行"],
    ]


def test_batches_stay_within_a_field_and_a_size():
    names = [Entry(f"名前{number}", ("shop:name",)) for number in range(ITEMS + 1)]
    long = [
        Entry("説" * (CHARACTERS // 2 + 1), ("shop:text",)),
        Entry("明" * (CHARACTERS // 2 + 1), ("shop:text",)),
    ]
    batches = list(packed(names + long))
    assert [(batch.origin, len(batch.texts)) for batch in batches] == [
        ("shop:name", ITEMS),
        ("shop:name", 1),
        ("shop:text", 1),
        ("shop:text", 1),
    ]


def test_a_later_line_of_the_memory_corrects_an_earlier_one(tmp_path):
    path = tmp_path / "zh.jsonl"
    with Memory(path) as memory:
        memory.record([("リン", "琳"), ("スズ", "铃")])
        memory.record([("リン", "凛")])
    kept = Memory(path)
    assert (kept.get("リン"), kept.get("スズ"), len(kept), "リン" in kept) == (
        "凛",
        "铃",
        2,
        True,
    )
    assert b"\r" not in path.read_bytes()
