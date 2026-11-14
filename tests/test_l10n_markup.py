import pytest

from monmusu_l10n.glossary import TERM_LENGTH, Glossary, is_term, layers
from monmusu_l10n.markup import fit, problem


SOURCE = "<color=yellow>攻撃力</color>が{0}%アップ\n[duration]秒間"


def test_a_faithful_translation_passes():
    assert (
        problem(SOURCE, "<color=yellow>攻击力</color>提升{0}%\n持续[duration]秒")
        is None
    )
    assert (
        problem('<link="火傷">火傷</link>を付与', '附加<link="火傷">烧伤</link>')
        is None
    )
    assert problem("[awaked,+0.5倍]アップ", "提升[awaked,+0.5倍]") is None
    assert problem("ヽ(´▽`)ノ わーい", "ヽ(´▽`)ノ 好耶") is None


@pytest.mark.parametrize(
    ("target", "told"),
    [
        ("攻击力提升{0}%\n持续[duration]秒", "missing </color> x1, <color=yellow> x1"),
        ("<color=yellow>攻击力</color>提升{0}%\n持续秒", "missing [duration] x1"),
        ("<color=yellow>攻击力</color>提升%\n持续[duration]秒", "missing {0} x1"),
        (
            "<color=yellow>攻击力</color>提升{0}%，持续[duration]秒",
            "missing line break x1",
        ),
        (
            "<color=yellow>攻击力</color>提升{0}%\n\n持续[duration]秒",
            "not in the source: line break x1",
        ),
    ],
)
def test_markup_has_to_match_the_source(target, told):
    assert problem(SOURCE, target) == f"markup differs from the source: {told}"


def test_square_brackets_around_a_name_stay_square():
    assert (
        problem("[インキュバス娘]タンタル", "【男梦魔娘】坦塔尔")
        == "markup differs from the source: missing [ x1, ] x1"
    )
    assert problem("[インキュバス娘]タンタル", "[男梦魔娘]坦塔尔") is None


def test_text_left_in_japanese_is_caught():
    assert (
        problem("オルフェリアーヌが仲間になる", "オルフェリアーヌ成为伙伴")
        == "Japanese kana is left untranslated"
    )
    assert (
        problem("攻撃時に発動", "攻击時发动")
        == "Japanese letterforms are carried over: 時"
    )
    assert problem("攻撃時に発動", "攻击时发动") is None


def test_an_answer_that_is_no_translation_is_caught():
    assert problem("リン", "") == "no translation was given"
    assert problem("リン", None) == "no translation was given"
    assert (
        problem("リン", "琳" * 60) == "the translation runs far longer than the source"
    )


def test_blank_space_around_a_text_is_kept():
    assert fit("　リン\n", "琳") == "　琳\n"
    assert fit("リン", " 琳 ") == "琳"
    assert fit("  ", "琳") == "琳"


def test_names_are_what_a_glossary_holds():
    assert is_term("リン")
    assert is_term("幻獣石")
    assert not is_term("は")
    assert not is_term("ありがとう")
    assert not is_term("<color=red>リン</color>")
    assert not is_term("リン\nスズ")
    assert not is_term("石" * (TERM_LENGTH + 1))


def test_the_longest_name_at_each_place_is_the_one_given():
    glossary = Glossary(
        {
            "ヴィーヴル": "薇薇尔",
            "ヴィーヴル娘": "薇薇尔娘",
            "娘": "少女",
            "幻獣石": "幻兽石",
        }
    )
    assert glossary.within("【ヴィーヴル娘】に幻獣石を渡す娘", 10) == {
        "ヴィーヴル娘": "薇薇尔娘",
        "幻獣石": "幻兽石",
        "娘": "少女",
    }
    assert glossary.within("幻獣石", 10) == {}
    assert len(glossary) == 4


def test_a_crowded_text_keeps_its_longest_names():
    glossary = Glossary({"ヴィーヴル娘": "薇薇尔娘", "幻獣石": "幻兽石", "リン": "琳"})
    assert glossary.within("リンとヴィーヴル娘と幻獣石", 2) == {
        "ヴィーヴル娘": "薇薇尔娘",
        "幻獣石": "幻兽石",
    }


def test_text_shown_by_a_command_is_translated():
    source = "[notawaked,まれに]攻撃[awaked,攻撃力+4%]"
    assert problem(source, "[notawaked,偶尔]攻击[awaked,攻击力+4%]") is None
    assert (
        problem(source, "[notawaked,まれに]攻击[awaked,攻击力+4%]")
        == "Japanese kana is left untranslated"
    )
    assert (
        problem(source, "偶尔攻击[awaked,攻击力+4%]")
        == "markup differs from the source: missing [notawaked,…] x1"
    )
    linked = '[awaked,<color=yellow><link="超過電">超過電</link></color>]を付与'
    kept = '附加[awaked,<color=yellow><link="超過電">超过电</link></color>]'
    assert problem(linked, kept) is None


def test_a_command_without_text_stays_as_written():
    assert problem("[awaked,+10%]アップ", "提升[awaked,+15%]") == (
        "markup differs from the source: missing [awaked,+10%] x1; "
        "not in the source: [awaked,+15%] x1"
    )


def test_a_name_inside_a_longer_word_is_not_that_name():
    glossary = Glossary({"ショップ": "商店", "モン娘": "魔物娘"})
    assert glossary.within("ビショップフィッシュ娘とデーモン娘", 10) == {}
    assert glossary.within("ショップでモン娘を選ぶ", 10) == {
        "ショップ": "商店",
        "モン娘": "魔物娘",
    }


def test_no_kana_is_left_inside_a_command():
    source = "長い説明文" * 8 + "[awaked,の]"
    kept = "很长的说明文" * 8
    assert problem(source, kept + "[awaked,の]") == "Japanese kana is left untranslated"
    assert problem(source, kept + "[awaked,的]") is None


def test_a_name_in_a_tag_is_not_one_to_render():
    glossary = Glossary({"火傷": "烧伤"})
    assert glossary.within('<link="火傷">やけど</link>を付与', 10) == {}
    assert glossary.within('<link="火傷">火傷</link>を付与', 10) == {"火傷": "烧伤"}


def test_a_settled_name_has_to_be_kept():
    glossary = Glossary({"アメリ": "阿梅莉"})
    assert glossary.unkept("ゴースト娘アメリ", "幽灵娘阿梅莉", 10) is None
    assert (
        glossary.unkept("ゴースト娘アメリ", "幽灵娘艾梅莉", 10)
        == "アメリ has to be rendered as 阿梅莉"
    )


def test_names_are_layered_by_the_names_they_hold():
    names = ["水着リン", "リン", "【ヴィーヴル娘】水着リン", "ヴィーヴル娘"]
    assert layers(names) == {
        "リン": 0,
        "ヴィーヴル娘": 0,
        "水着リン": 1,
        "【ヴィーヴル娘】水着リン": 2,
    }


def test_a_reading_set_over_a_word_is_dropped():
    source = "<ruby=ひだ>襞</ruby>で<size=40>撫でる</size>"
    assert problem(source, "用褶皱<size=40>抚摸</size>") is None
    kept = problem(source, "用<ruby=ひだ>褶皱</ruby><size=40>抚摸</size>")
    assert "not in the source: </ruby> x1, <ruby=ひだ> x1" in kept


def test_a_glossary_narrows_to_the_names_asked_for():
    names = Glossary({"リン": "琳", "スズ": "铃", "クロミ": "库洛米"})
    kept = names.only(["スズ", "クロミ", "ルカ"])
    assert (len(names), len(kept)) == (3, 2)
    assert kept.within("リンとスズとクロミ", 9) == {"スズ": "铃", "クロミ": "库洛米"}


def test_a_number_inside_a_longer_number_is_not_that_number():
    chapters = Glossary({"3章": "第3章"})
    assert chapters.within("13章と3章", 9) == {"3章": "第3章"}
    assert chapters.unkept("13章", "第13章", 9) is None
    assert chapters.unkept("１３章", "第13章", 9) is None


def test_a_name_is_kept_through_the_tags_between_its_letters():
    names = Glossary({"フレンド": "好友"})
    source = "フ<cspace=-2>レ<cspace=-3>ン<cspace=1>ドPt"
    assert names.unkept(source, "好<cspace=-2>友<cspace=-3>Pt<cspace=1>", 9) is None
    assert names.unkept(source, "朋<cspace=-2>友<cspace=-3>Pt<cspace=1>", 9)


def test_a_character_that_could_not_be_read_is_caught():
    assert "could not be read" in problem("みんな密猟されちゃう", "大家都�被偷猎走了")
