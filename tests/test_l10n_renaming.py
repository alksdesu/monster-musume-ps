import json

import pytest

from monmusu_l10n.brief import Brief
from monmusu_l10n.catalog import Entry
from monmusu_l10n.memory import Memory
from monmusu_l10n.renaming import apply, rewritten, Move


BRIEF = Brief("风格", "name$", cast="^scenario_cast:name$")
ENTRIES = [
    Entry("セレン", ("scenario_cast:name", "unitcard:name")),
    Entry("サウラ", ("scenario_cast:name",)),
    Entry("サウラン", ("scenario_cast:name",)),
    Entry("ゴブリン", ("enemydata:name",)),
    Entry("セレンの剣", ("item_template:name",)),
    Entry("セレンとサウランが来た。", ("scenario:a",)),
    Entry("サウラは笑った。", ("scenario:a",)),
    Entry("塞伦という名の村", ("scenario:a",)),
    Entry("セレンは「セレンちゃん」と呼ばれた。", ("scenario:a",)),
    Entry("<ruby=セレン>彼女</ruby>が来た。", ("scenario:a",)),
]
KNOWN = {
    "セレン": "塞伦",
    "サウラ": "萨乌拉",
    "サウラン": "萨乌兰",
    "ゴブリン": "哥布林",
    "セレンの剣": "塞伦之剑",
    "セレンとサウランが来た。": "塞伦和萨乌兰来了。",
    "サウラは笑った。": "萨乌拉笑了。",
    "塞伦という名の村": "名叫塞伦的村子",
    "セレンは「セレンちゃん」と呼ばれた。": "塞伦被叫做「小塞伦」。",
    "<ruby=セレン>彼女</ruby>が来た。": "<ruby=セレン>她</ruby>来了。",
    "セレンが好き。": "喜欢小赛。",
}


@pytest.fixture
def memory(tmp_path):
    path = tmp_path / "zh-Hans.jsonl"
    with Memory(path) as writing:
        writing.record(KNOWN.items())
        # An amended line, so the rewrite has something to fold away.
        writing.record([("ゴブリン", "哥布林")])
    return Memory(path)


def lines(memory):
    return [json.loads(line) for line in memory.path.read_text(encoding="utf-8").splitlines()]


def test_names_and_the_texts_naming_them_move_together(memory):
    outcome = apply({"セレン": "赛莲", "サウラ": "萨拉", "サウラン": "萨乌兰"}, ENTRIES, memory, BRIEF)
    assert [(move.source, move.before, move.after) for move in outcome.moves] == [
        ("セレン", "塞伦", "赛莲"),
        ("サウラ", "萨乌拉", "萨拉"),
    ]
    assert memory.get("セレン") == "赛莲"
    assert memory.get("セレンの剣") == "赛莲之剑"
    assert memory.get("セレンとサウランが来た。") == "赛莲和萨乌兰来了。"
    assert memory.get("サウラは笑った。") == "萨拉笑了。"
    assert memory.get("セレンは「セレンちゃん」と呼ばれた。") == "赛莲被叫做「小赛莲」。"
    assert outcome.rewritten == 4
    assert outcome.unknown == []


def test_a_rendering_is_only_touched_where_its_name_is_written(memory):
    apply({"セレン": "赛莲"}, ENTRIES, memory, BRIEF)
    assert memory.get("塞伦という名の村") == "名叫塞伦的村子"
    assert memory.get("<ruby=セレン>彼女</ruby>が来た。") == "<ruby=セレン>她</ruby>来了。"
    assert memory.get("サウラン") == "萨乌兰"


def test_texts_without_the_old_rendering_are_reported_not_guessed(memory):
    outcome = apply({"セレン": "赛莲"}, ENTRIES, memory, BRIEF)
    assert outcome.unresolved == ["セレンが好き。"]
    assert memory.get("セレンが好き。") == "喜欢小赛。"


def test_names_outside_the_cast_and_unchanged_names_are_left_alone(memory):
    outcome = apply({"ゴブリン": "地精", "サウラン": "萨乌兰", "リン": "琳"}, ENTRIES, memory, BRIEF)
    assert outcome.moves == [] and outcome.rewritten == 0
    assert sorted(outcome.unknown) == ["ゴブリン", "リン"]
    assert memory.get("ゴブリン") == "哥布林"


def test_the_file_is_rewritten_once_without_amendment_lines(memory):
    before = len(lines(memory))
    assert before == len(KNOWN) + 1
    apply({"セレン": "赛莲"}, ENTRIES, memory, BRIEF)
    written = lines(memory)
    assert len(written) == len(KNOWN)
    assert {pair["source"]: pair["target"] for pair in written} == dict(memory.items())
    assert Memory(memory.path).get("セレンの剣") == "赛莲之剑"


def test_renderings_swap_in_one_pass_so_chains_do_not_form():
    held = [Move("ア", "甲", "乙"), Move("イ", "乙", "丙"), Move("ウ", "乙丙", "丁")]
    assert rewritten("甲乙乙丙", held) == "乙丙丁"
    assert rewritten("无关", held) == "无关"


def test_an_open_memory_cannot_be_rewritten(tmp_path):
    with Memory(tmp_path / "m.jsonl") as writing:
        with pytest.raises(RuntimeError):
            writing.rewrite([("a", "b")])


def test_single_character_renderings_change_the_name_but_not_other_texts(tmp_path):
    entries = [Entry("きつね", ("scenario_cast:name",)), Entry("妖狐のきつね", ("scenario:a",))]
    path = tmp_path / "m.jsonl"
    with Memory(path) as writing:
        writing.record([("きつね", "狐"), ("妖狐のきつね", "妖狐的狐")])
    memory = Memory(path)
    outcome = apply({"きつね": "双尾"}, entries, memory, BRIEF)
    assert memory.get("きつね") == "双尾"
    assert memory.get("妖狐のきつね") == "妖狐的狐"
    assert outcome.rewritten == 0 and outcome.unresolved == ["妖狐のきつね"]


def test_a_composite_offer_that_drops_its_parts_is_not_followed(tmp_path):
    entries = [Entry("ティヴェル", ("scenario_cast:name",)), Entry("typeM（ティヴェル）", ("scenario_cast:name",))]
    path = tmp_path / "m.jsonl"
    with Memory(path) as writing:
        writing.record([("ティヴェル", "蒂维尔"), ("typeM（ティヴェル）", "typeM（蒂维尔）")])
    memory = Memory(path)
    outcome = apply({"ティヴェル": "蒂薇儿", "typeM（ティヴェル）": "typeM（蒂维尔）"}, entries, memory, BRIEF)
    assert [move.source for move in outcome.moves] == ["ティヴェル"]
    assert memory.get("typeM（ティヴェル）") == "typeM（蒂薇儿）"
    assert outcome.unresolved == []
