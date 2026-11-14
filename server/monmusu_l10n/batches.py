from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .catalog import Entry
from .glossary import is_term, layers


ITEMS = 40
CHARACTERS = 2400


@dataclass(frozen=True)
class Batch:
    origin: str
    texts: tuple[str, ...]

    def halves(self) -> tuple["Batch", "Batch"]:
        middle = len(self.texts) // 2
        return Batch(self.origin, self.texts[:middle]), Batch(
            self.origin, self.texts[middle:]
        )

    def only(self, texts: Iterable[str]) -> "Batch":
        return Batch(self.origin, tuple(texts))


def waves(entries: Iterable[Entry]) -> list[list[Entry]]:
    # Names go first, each after the shorter names inside it; sentences come last.
    entries = list(entries)
    depth = layers(entry.text for entry in entries if is_term(entry.text))
    last = max(depth.values(), default=-1) + 1
    grouped: list[list[Entry]] = [[] for _ in range(last + 1)]
    for entry in entries:
        grouped[depth.get(entry.text, last)].append(entry)
    return grouped


def packed(entries: Iterable[Entry]) -> Iterator[Batch]:
    origin, texts, size = None, [], 0
    for entry in entries:
        full = len(texts) == ITEMS or size + len(entry.text) > CHARACTERS
        if texts and (entry.origin != origin or full):
            yield Batch(origin, tuple(texts))
            texts, size = [], 0
        origin = entry.origin
        texts.append(entry.text)
        size += len(entry.text)
    if texts:
        yield Batch(origin, tuple(texts))
