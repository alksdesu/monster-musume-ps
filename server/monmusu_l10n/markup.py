import re
from collections import Counter


JAPANESE = re.compile(r"[\u3005\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")
TAG = re.compile(r"</?[A-Za-z][^<>\n]*>")
# A reading set over a word means nothing in Chinese, so a translation drops the pair.
RUBY = re.compile(r"</?ruby\b[^<>\n]*>")
TOKEN = re.compile(r"\[(?:[A-Za-z_][A-Za-z0-9_]*|\d+)(?:,[^\[\]\n]*)?\]")
SLOT = re.compile(r"\{\d+(?::[^{}\n]*)?\}")
FIXED = (TAG, TOKEN, SLOT)
BREAK = "\n"
BREAK_NAME = "line break"
SQUARE = "[]"
SHOWN = "…"
# The middle dot and the long vowel mark sit in the kana block but are used in Chinese text too.
KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\uff66-\uff9d]")
KANA_ALLOWED = 2
KANA_SHARE = 20
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
SIMPLIFIED = "gb2312"
GROWTH = 4
# A reply broken off inside a character carries this mark where the character was.
LOST = "\ufffd"
GROWTH_ROOM = 40


def _command(token: str) -> str:
    head, _, shown = token[1:-1].partition(",")
    # What follows the comma is put on screen, so once it is text it gets translated.
    return f"[{head},{SHOWN}]" if JAPANESE.search(TAG.sub("", shown)) else token


def fixed(text: str) -> Counter[str]:
    parts = Counter(match for pattern in (TAG, SLOT) for match in pattern.findall(text))
    parts.update(_command(token) for token in TOKEN.findall(text))
    # The game reads square brackets as commands, so a name set in them keeps them too.
    loose = TOKEN.sub("", text)
    counted = {
        BREAK_NAME: text.count(BREAK),
        **{bracket: loose.count(bracket) for bracket in SQUARE},
    }
    parts.update({name: count for name, count in counted.items() if count})
    return parts


def marked(text: str) -> bool:
    return any(pattern.search(text) for pattern in FIXED)


def fit(source: str, target: str) -> str:
    # Blank space around a text is layout the game relies on, and models trim it.
    body = source.strip()
    if not body:
        return target
    start = source.index(body)
    return source[:start] + target.strip() + source[start + len(body) :]


def _simplified(character: str) -> bool:
    try:
        character.encode(SIMPLIFIED)
    except UnicodeEncodeError:
        return False
    return True


def _listed(parts: Counter[str]) -> str:
    return ", ".join(f"{part} x{count}" for part, count in sorted(parts.items()))


def problem(source: str, target: object) -> str | None:
    if not isinstance(target, str) or not target.strip():
        return "no translation was given"
    if LOST in target:
        return "the translation holds a character that could not be read"
    wanted, given = fixed(RUBY.sub("", source)), fixed(target)
    if wanted != given:
        missing, extra = wanted - given, given - wanted
        told = [
            f"missing {_listed(missing)}" if missing else "",
            f"not in the source: {_listed(extra)}" if extra else "",
        ]
        return "markup differs from the source: " + "; ".join(filter(None, told))
    plain = TAG.sub("", target)
    # A command shows a few words at most, so kana in one is never an emoticon.
    commanded = any(KANA.search(token) for token in TOKEN.findall(plain))
    spare = max(KANA_ALLOWED, len(plain) // KANA_SHARE)
    if commanded or len(KANA.findall(plain)) > spare:
        return "Japanese kana is left untranslated"
    # A character outside simplified Chinese that the source also has was copied, not translated.
    carried = sorted(
        {
            character
            for character in HAN.findall(plain)
            if character in source and not _simplified(character)
        }
    )
    if carried:
        return f"Japanese letterforms are carried over: {''.join(carried)}"
    if len(target) > GROWTH * len(source) + GROWTH_ROOM:
        return "the translation runs far longer than the source"
    return None
