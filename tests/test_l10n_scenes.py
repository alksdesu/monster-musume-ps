import json

from monmusu_l10n import catalog, scenes
from monmusu_l10n.scenes import Line, Scene


HEAD = ("", "Command", "Arg1", "Arg2", "Text")


def written(root, path, document):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def grid(name, head, *rows, hidden=()):
    listed = [(head, False), *((row, row in hidden) for row in rows)]
    return {
        "name": f"Assets/AdvScene/ScenarioExcel/{name}",
        "headerRow": 0,
        "rows": [
            {"strings": list(row), "isCommentOut": int(out)} for row, out in listed
        ],
    }


def book(root, path, *grids):
    written(
        root, f"scenarios/{scenes.SCRIPTS}/{path}.book.json", {"importGridList": grids}
    )


def game(tmp_path):
    written(
        tmp_path,
        f"scenarios/{scenes.SETTINGS}",
        {
            "settingList": [
                grid(
                    "config/cfg_character.xlsx:Character",
                    ("", "CharacterName", "", "NameText"),
                    ("", "リン水着", "ヴィーヴル", "<param=ch10006>"),
                    ("", "カミラ", "", "密猟神官"),
                    ("", "スズ", "", ""),
                ),
                grid(
                    "config/cfg_param.xlsx:Param",
                    ("", "Label", "Type", "Value"),
                    ("//キャラ名の一括管理",),
                    ("", "ch10006", "String", "リン"),
                    ("", "flag", "Bool", "false"),
                    hidden=[("//キャラ名の一括管理",)],
                ),
            ]
        },
    )
    muted = ("", "", "リン水着", "", "消した台詞")
    book(
        tmp_path,
        "character/10006/10006A0_Nml",
        grid(
            "character/10006/10006A0_Nml.xlsx:10006A0_Nml",
            HEAD,
            ("", "Bg", "背景_森", "", ""),
            ("", "", "", "", "朝の森は静かだ。"),
            ("", "", "リン水着", "Emt1_Nor", "おはよう、ご主人様！"),
            ("", "", "リン水着", "", "……！"),
            muted,
            ("", "Param", ' ch10006 = "リン（？）" ', "", ""),
            ("", "", "リン水着", "", "わたしは誰でしょう？"),
            ("", "", "カミラ", "", "捕まえた。"),
            ("", "", "スズ", "", "待って！"),
            ("", "", "謎の声", "", "ふふふ……甘いね。"),
            ("", "Selection", "*逃げる", "", "逃げる"),
            ("", "*逃げる", "", "", "ラベルの行"),
            hidden=[muted],
        ),
        grid(
            "character/10006/10006A0_Nml.xlsx:続き",
            HEAD,
            ("", "", "リン水着", "", "まだ続くよ。"),
        ),
    )
    book(
        tmp_path,
        "character/10006/10006A1_R18",
        grid(
            "character/10006/10006A1_R18.xlsx:10006A1_R18",
            ("", "Command", "Arg1", "Text"),
            ("", "", "リン水着", "二人きりだね。"),
        ),
    )
    written(
        tmp_path,
        "tables/story_data_unit.json",
        {
            "table": [
                {"scenario_name": "10006A0_Nml", "title": "森の朝", "r18": 0},
                {"scenario_name": "10006A1_R18", "title": "森の夜", "r18": 0},
                {"scenario_name": "10006A1_R18", "title": "再録", "r18": 1},
            ]
        },
    )
    return scenes.collect(tmp_path / "scenarios", tmp_path / "tables")


def test_a_scene_is_read_as_who_says_what_in_order(tmp_path):
    first, second, adult = game(tmp_path)
    assert (first.origin, first.title) == (
        "scenario:character/10006/10006A0_Nml",
        "森の朝",
    )
    assert first.lines == (
        Line("", "朝の森は静かだ。"),
        Line("リン", "おはよう、ご主人様！"),
        Line("リン（？）", "わたしは誰でしょう？"),
        Line("密猟神官", "捕まえた。"),
        Line("スズ", "待って！"),
        Line("謎の声", "ふふふ……甘いね。"),
        Line("", "逃げる", True),
    )
    assert second == Scene(
        "scenario:character/10006/10006A0_Nml#続き",
        "森の朝",
        (Line("リン", "まだ続くよ。"),),
    )
    assert adult == Scene(
        "scenario_r18:character/10006/10006A1_R18",
        "森の夜",
        (Line("リン", "二人きりだね。"),),
    )


def test_the_names_shown_over_talk_are_texts_of_their_own(tmp_path):
    told = game(tmp_path)
    assert scenes.names(told) == [
        (scenes.CAST, name)
        for name in ("スズ", "リン", "リン（？）", "密猟神官", "謎の声")
    ]
    written(tmp_path, "masters/unitcard/10006.json", {"charaName": "リン"})
    found = catalog.collect(tmp_path / "masters", scenes.names(told))
    assert {entry.text: entry.origins for entry in found} == {
        "スズ": ("scenario_cast:name",),
        "リン": ("scenario_cast:name", "unitcard:charaName"),
        "リン（？）": ("scenario_cast:name",),
        "密猟神官": ("scenario_cast:name",),
        "謎の声": ("scenario_cast:name",),
    }


def test_scenes_read_back_as_they_were_saved(tmp_path):
    told = game(tmp_path)
    path = tmp_path / "out" / "scenes.jsonl"
    scenes.save(told, path)
    assert scenes.load(path) == told
    assert b"\r" not in path.read_bytes()
    assert scenes.load(tmp_path / "out" / "absent.jsonl") == []


def test_a_game_without_story_scripts_has_no_scenes(tmp_path):
    assert scenes.collect(tmp_path / "scenarios", tmp_path / "tables") == []
