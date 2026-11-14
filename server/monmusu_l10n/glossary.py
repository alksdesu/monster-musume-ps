import re
from collections.abc import Iterable, Mapping

from .markup import BREAK, TAG, marked


TERM_LENGTH = 24
SHORTEST = 2
# Words written only in hiragana are grammar, not names.
NAMING = re.compile(r"[\u30a1-\u30fa\u3005\u3400-\u4dbf\u4e00-\u9fff]")
# A run of katakana is one word and a run of digits one number; a name inside a longer run is neither.
RUNS = (re.compile(r"[\u30a1-\u30fa\u30fc]"), re.compile(r"[0-9\uff10-\uff19]"))


def is_term(text: str) -> bool:
    sized = SHORTEST <= len(text) <= TERM_LENGTH
    return (
        sized and BREAK not in text and not marked(text) and bool(NAMING.search(text))
    )


def _joined(text: str, at: int) -> bool:
    return 0 < at < len(text) and any(
        all(run.match(text[place]) for place in (at - 1, at)) for run in RUNS
    )


class Glossary:
    def __init__(self, terms: Mapping[str, str]):
        self._terms = dict(terms)
        self._by_head: dict[str, list[tuple[str, str]]] = {}
        for source, target in terms.items():
            self._by_head.setdefault(source[0], []).append((source, target))
        for candidates in self._by_head.values():
            candidates.sort(key=lambda pair: len(pair[0]), reverse=True)

    def __len__(self) -> int:
        return len(self._terms)

    def only(self, sources: Iterable[str]) -> "Glossary":
        kept = self._terms.keys() & set(sources)
        return Glossary({source: self._terms[source] for source in kept})

    def within(self, text: str, limit: int) -> dict[str, str]:
        # What a tag carries is never translated, so a name there is not one to render.
        text = TAG.sub("", text)
        found: dict[str, str] = {}
        position = 0
        while position < len(text):
            for source, target in self._by_head.get(text[position], ()):
                # A text is never its own reference, or a name would pin itself.
                end = position + len(source)
                apart = not (_joined(text, position) or _joined(text, end))
                if source != text and text.startswith(source, position) and apart:
                    found[source] = target
                    position += len(source)
                    break
            else:
                position += 1
        if len(found) <= limit:
            return found
        # The longest names are the ones a translator is least likely to guess the same way twice.
        kept = sorted(found, key=len, reverse=True)[:limit]
        return {source: found[source] for source in found if source in kept}

    def unkept(self, text: str, target: str, limit: int) -> str | None:
        # Spacing tags may sit between the letters of a name, in a translation as in its source.
        shown = TAG.sub("", target)
        for source, wanted in self.within(text, limit).items():
            if wanted not in shown:
                return f"{source} has to be rendered as {wanted}"
        return None


def layers(names: Iterable[str]) -> dict[str, int]:
    # A name is settled after every shorter name written inside it.
    ordered = sorted(set(names), key=len)
    inside = Glossary(dict.fromkeys(ordered, ""))
    depth: dict[str, int] = {}
    for name in ordered:
        held = inside.within(name, len(name))
        depth[name] = 1 + max((depth[inner] for inner in held), default=-1)
    return depth
