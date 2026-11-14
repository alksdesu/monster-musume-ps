import json

import pytest

from monmusu_l10n.batches import ITEMS
from monmusu_l10n.brief import Brief
from monmusu_l10n.catalog import Entry
from monmusu_l10n.memory import Memory
from monmusu_l10n.scenes import Line, Scene
from monmusu_l10n.service import Refused, Reply, Usage
from monmusu_l10n.translator import (
    ALONE,
    BEFORE,
    CHOICE,
    EXAMPLES,
    NARRATION,
    Failure,
    Translator,
)


BRIEF = Brief("风格", "name$", {"unit": "魔物娘的名字"}, {"幻獣石": "幻兽石"})
KNOWN = {"リン": "琳", "オルフェリアーヌ": "奥尔菲莉亚努", "アメリ": "阿梅莉"}
SENTENCE = "リンが仲間になる。幻獣石を１００個獲得できる特別なパック"
KANA_LEFT = "Japanese kana is left untranslated"


class FakeChat:
    def __init__(self, answer):
        self.answer = answer
        self.usage = Usage()
        self.asked: list[dict] = []

    async def ask(self, system: str, user: str) -> Reply:
        request = json.loads(user)
        self.asked.append(request)
        result = self.answer(request)
        if isinstance(result, Reply):
            return result
        return Reply(
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False),
            True,
        )


def echo(request: dict) -> dict:
    # A stand-in translator that at least uses the names it is given.
    named = "".join(request["glossary"].values())
    return {
        number: KNOWN.get(text, f"{named}译文{number}")
        for number, text in request["texts"].items()
    }


def entries(origin: str, *texts: str) -> list[Entry]:
    return [Entry(text, (origin,)) for text in texts]


def scene(origin: str, *lines: tuple, title: str = "") -> Scene:
    return Scene(origin, title, tuple(Line(*line) for line in lines))


async def translated(
    tmp_path, items, answer=echo, known=(), asked=None, scenes=(), brief=BRIEF
):
    chat = FakeChat(answer)
    with Memory(tmp_path / "zh.jsonl") as memory:
        memory.record(known)
        translator = Translator(
            chat, memory, brief, items, scenes, 4, lambda line: None
        )
        tally = await translator.run(items if asked is None else asked, scenes)
    return chat, memory, tally


async def test_names_are_settled_before_the_sentences_that_use_them(tmp_path):
    items = entries("shop:text", SENTENCE) + entries("unit:name", "リン")
    chat, memory, tally = await translated(tmp_path, items)
    first, second = chat.asked
    assert first["texts"] == {"1": "リン"}
    assert first["context"] == "魔物娘的名字（unit:name）"
    assert second["texts"] == {"1": SENTENCE}
    assert second["glossary"] == {"リン": "琳", "幻獣石": "幻兽石"}
    assert (memory.get("リン"), tally.translated, tally.failures) == ("琳", 2, [])


async def test_later_batches_follow_the_first_of_their_field(tmp_path):
    names = [f"名前{number:02}" for number in range(ITEMS + 5)]
    chat, memory, tally = await translated(tmp_path, entries("shop:name", *names))
    first, second = chat.asked
    assert (len(first["texts"]), first["examples"]) == (ITEMS, {})
    assert len(second["examples"]) == EXAMPLES
    assert second["examples"] == {name: memory.get(name) for name in names[:EXAMPLES]}
    assert tally.translated == len(names)


async def test_a_bad_answer_is_asked_again_with_what_was_wrong(tmp_path):
    def answer(request):
        return echo(request) if "faults" in request else dict(request["texts"])

    chat, memory, tally = await translated(
        tmp_path, entries("unit:name", "オルフェリアーヌ"), answer
    )
    assert "faults" not in chat.asked[0]
    assert chat.asked[1]["faults"] == {"1": KANA_LEFT}
    assert (memory.get("オルフェリアーヌ"), tally.translated, tally.failures) == (
        "奥尔菲莉亚努",
        1,
        [],
    )


async def test_a_text_that_keeps_failing_is_reported(tmp_path):
    chat, memory, tally = await translated(
        tmp_path,
        entries("unit:name", "オルフェリアーヌ"),
        lambda request: dict(request["texts"]),
    )
    assert len(chat.asked) == ALONE
    assert tally.failures == [Failure("オルフェリアーヌ", ("unit:name",), KANA_LEFT)]
    assert ("オルフェリアーヌ" in memory, tally.translated) == (False, 0)


async def test_a_refused_batch_is_narrowed_to_the_text_at_fault(tmp_path):
    def answer(request):
        if any("禁句" in text for text in request["texts"].values()):
            raise Refused("400: Content Exists Risk")
        return echo(request)

    texts = ("安全な文一", "安全な文二", "安全な文三", "禁句の文")
    chat, memory, tally = await translated(
        tmp_path, entries("shop:text", *texts), answer
    )
    assert [text in memory for text in texts] == [True, True, True, False]
    assert [(failure.text, failure.problem) for failure in tally.failures] == [
        ("禁句の文", "the service refused it: 400: Content Exists Risk")
    ]


@pytest.mark.parametrize(
    ("spoiled", "told"),
    [
        ("oops", "the reply was not a JSON object"),
        ("[]", "the reply was not a JSON object"),
        (Reply('{"1": "琳"}', False), "the reply was cut short"),
        ({"2": "琳"}, "no translation was given"),
    ],
)
async def test_a_spoiled_reply_is_asked_again(tmp_path, spoiled, told):
    chat, memory, tally = await translated(
        tmp_path,
        entries("unit:name", "リン"),
        lambda request: echo(request) if "faults" in request else spoiled,
    )
    assert chat.asked[1]["faults"] == {"1": told}
    assert (memory.get("リン"), tally.failures) == ("琳", [])


async def test_what_is_already_known_is_not_asked_again(tmp_path):
    items = entries("item:name", "幻獣石") + entries("unit:name", "リン")
    chat, memory, tally = await translated(tmp_path, items, known=[("リン", "琳")])
    assert chat.asked == []
    assert (memory.get("幻獣石"), tally.wanted, tally.translated) == ("幻兽石", 0, 0)
    assert Memory(tmp_path / "zh.jsonl").get("幻獣石") == "幻兽石"


async def test_a_kept_translation_the_checks_reject_is_made_again(tmp_path):
    name = "オルフェリアーヌ"
    chat, memory, tally = await translated(
        tmp_path, entries("unit:name", name), known=[(name, name)]
    )
    assert len(chat.asked) == 1
    assert (memory.get(name), tally.wanted, tally.translated) == (KNOWN[name], 1, 1)


async def test_a_name_keeps_the_shorter_name_inside_it(tmp_path):
    def answer(request):
        fresh = (
            "faults" not in request and "ゴースト娘アメリ" in request["texts"].values()
        )
        return {"1": "幽灵娘艾梅莉"} if fresh else echo(request)

    items = entries("unit:name", "ゴースト娘アメリ", "アメリ")
    chat, memory, tally = await translated(tmp_path, items, answer)
    assert [list(request["texts"].values()) for request in chat.asked] == [
        ["アメリ"],
        ["ゴースト娘アメリ"],
        ["ゴースト娘アメリ"],
    ]
    assert chat.asked[1]["glossary"] == {"アメリ": "阿梅莉"}
    assert chat.asked[2]["faults"] == {"1": "アメリ has to be rendered as 阿梅莉"}
    assert (memory.get("ゴースト娘アメリ"), tally.failures) == ("阿梅莉译文1", [])


async def test_a_sentence_is_made_again_when_a_name_in_it_changes(tmp_path):
    items = entries("shop:text", SENTENCE) + entries("unit:name", "リン")
    kept = [("リン", "琳"), (SENTENCE, "凛成为伙伴。可获得幻兽石")]
    chat, memory, tally = await translated(tmp_path, items, known=kept)
    assert [request["texts"] for request in chat.asked] == [{"1": SENTENCE}]
    assert ("琳" in memory.get(SENTENCE), tally.wanted, tally.translated) == (
        True,
        1,
        1,
    )


async def test_names_come_from_everything_not_only_what_is_asked_for(tmp_path):
    items = entries("shop:text", SENTENCE) + entries("unit:name", "リン")
    chat, memory, tally = await translated(
        tmp_path, items, known=[("リン", "琳")], asked=items[:1]
    )
    assert chat.asked[0]["glossary"] == {"リン": "琳", "幻獣石": "幻兽石"}
    assert tally.translated == 1


SPOKEN = Brief(
    "风格",
    "name$",
    {"scenario": "剧情里的对话"},
    {"幻獣石": "幻兽石", "天井": "保底"},
    "^cast:name$",
    {"幻獣石": "幻兽石"},
)
CEILING = "リンが天井を見上げた"


async def test_a_scene_is_told_in_order_with_who_speaks_and_what_came_before(
    tmp_path,
):
    spoken = [("リン", f"台詞その{number:02}だよ") for number in range(ITEMS + 2)]
    told = scene(
        "scenario:main/1",
        ("", "朝の光が差す"),
        ("", "起きる", True),
        *spoken,
        title="朝の物語",
    )
    chat, memory, tally = await translated(
        tmp_path,
        entries("cast:name", "リン") + entries("story:title", "朝の物語"),
        scenes=[told],
        brief=SPOKEN,
    )
    first, second = chat.asked[-2:]
    assert first["context"] == "剧情里的对话（scenario:main/1）"
    assert (first["title"], first["before"]) == (memory.get("朝の物語"), [])
    assert "examples" not in first
    assert list(first["texts"].values()) == [line.text for line in told.lines[:ITEMS]]
    assert [first["speakers"][number] for number in "123"] == [
        NARRATION,
        CHOICE,
        "琳",
    ]
    last = told.lines[ITEMS - 1]
    assert len(second["before"]) == BEFORE
    assert second["before"][-1] == {
        "speaker": "琳",
        "text": last.text,
        "translation": memory.get(last.text),
    }
    assert list(second["texts"].values()) == [line.text for line in told.lines[ITEMS:]]
    assert (tally.translated, tally.failures) == (len(told.lines) + 2, [])


async def test_talk_is_held_to_who_people_are_not_to_menu_words(tmp_path):
    def answer(request):
        return {"1": "琳抬头看着天花板"} if "speakers" in request else echo(request)

    chat, memory, tally = await translated(
        tmp_path,
        entries("cast:name", "リン") + entries("shop:name", "天井"),
        answer,
        scenes=[scene("scenario:main/1", ("", CEILING))],
        brief=SPOKEN,
    )
    assert chat.asked[-1]["glossary"] == {"リン": "琳"}
    assert (memory.get(CEILING), tally.failures) == ("琳抬头看着天花板", [])


async def test_a_name_left_out_of_talk_is_asked_for_again(tmp_path):
    def answer(request):
        if "speakers" not in request:
            return echo(request)
        return {"1": "琳抬头看着天花板" if "faults" in request else "她抬头看着天花板"}

    chat, memory, tally = await translated(
        tmp_path,
        entries("cast:name", "リン"),
        answer,
        scenes=[scene("scenario:main/1", ("リン", CEILING))],
        brief=SPOKEN,
    )
    assert chat.asked[-1]["faults"] == {"1": "リン has to be rendered as 琳"}
    assert (memory.get(CEILING), tally.failures) == ("琳抬头看着天花板", [])


async def test_a_line_said_in_two_scenes_is_asked_once(tmp_path):
    told = [
        scene("scenario:main/1", ("リン", "おはよう"), ("リン", "今日は晴れだね")),
        scene("scenario:main/2", ("スズ", "おはよう"), ("スズ", "眠いなあ")),
    ]
    chat, memory, tally = await translated(tmp_path, [], scenes=told, brief=SPOKEN)
    asked = [text for request in chat.asked for text in request["texts"].values()]
    assert sorted(asked) == sorted(["おはよう", "今日は晴れだね", "眠いなあ"])
    assert tally.translated == 3


async def test_talk_already_translated_is_only_shown_as_what_came_before(tmp_path):
    told = scene("scenario:main/1", ("リン", "おはよう"), ("リン", "今日は晴れだね"))
    chat, memory, tally = await translated(
        tmp_path, [], known=[("おはよう", "早上好")], scenes=[told], brief=SPOKEN
    )
    (request,) = chat.asked
    assert request["texts"] == {"1": "今日は晴れだね"}
    assert request["before"] == [
        {"speaker": "リン", "text": "おはよう", "translation": "早上好"}
    ]
    assert tally.wanted == 1


async def test_a_name_is_a_plain_word_where_its_owner_is_not(tmp_path):
    def answer(request):
        return {"1": "来吃蜜瓜吧"} if "speakers" in request else echo(request)

    chat, memory, tally = await translated(
        tmp_path,
        entries("cast:name", "メロン", "スズ"),
        answer,
        known=[("メロン", "梅隆"), ("スズ", "铃")],
        scenes=[scene("scenario:main/1", ("スズ", "メロンを食べよう"))],
        brief=SPOKEN,
    )
    assert chat.asked[-1]["glossary"] == {"メロン": "梅隆"}
    assert (memory.get("メロンを食べよう"), tally.failures) == ("来吃蜜瓜吧", [])


async def test_whoever_speaks_in_a_scene_is_named_before_it_is_told(tmp_path):
    chat, memory, tally = await translated(
        tmp_path,
        entries("cast:name", "リン", "スズ"),
        asked=[],
        scenes=[scene("scenario:main/1", ("リン", "おはよう"))],
        brief=SPOKEN,
    )
    first, second = chat.asked
    assert first["texts"] == {"1": "リン"}
    assert second["speakers"] == {"1": "琳"}
    assert (memory.get("スズ"), tally.translated) == (None, 2)
