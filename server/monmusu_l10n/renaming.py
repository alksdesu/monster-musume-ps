import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .brief import Brief
from .catalog import Entry
from .glossary import Glossary
from .memory import Memory


# A single character cannot be told from the words around it, so it is never swapped inside a text.
SWAPPABLE = 2


@dataclass(frozen=True)
class Move:
    source: str
    before: str
    after: str

    @property
    def swappable(self) -> bool:
        return len(self.before) >= SWAPPABLE


@dataclass
class Outcome:
    moves: list[Move]
    unknown: list[str]
    rewritten: int = 0
    unresolved: list[str] = field(default_factory=list)


def moves(
    offered: Mapping[str, str], entries: Iterable[Entry], memory: Memory, brief: Brief
) -> tuple[list[Move], list[str]]:
    cast = {entry.text for entry in entries if brief.in_cast(entry.origins)}
    chosen: list[Move] = []
    unknown: list[str] = []
    for source, after in offered.items():
        if source not in cast:
            unknown.append(source)
            continue
        before = memory.get(source)
        if before is not None and before != after:
            chosen.append(Move(source, before, after))
    return consistent(chosen), unknown


def consistent(chosen: list[Move]) -> list[Move]:
    # A name made of other names takes its parts' renderings; an offer that drops them is not followed.
    inner = Glossary({move.source: move.after for move in chosen})
    return [
        move
        for move in chosen
        if all(
            after in move.after
            for after in inner.within(move.source, len(chosen)).values()
        )
    ]


def rewritten(target: str, held: Iterable[Move]) -> str:
    renderings = {move.before: move.after for move in held}
    if not renderings:
        return target
    # One pass over the text, longest first, so a new rendering is never read as an old one.
    pattern = re.compile(
        "|".join(map(re.escape, sorted(renderings, key=len, reverse=True)))
    )
    return pattern.sub(lambda match: renderings[match.group()], target)


def apply(
    offered: Mapping[str, str], entries: Iterable[Entry], memory: Memory, brief: Brief
) -> Outcome:
    chosen, unknown = moves(offered, entries, memory, brief)
    outcome = Outcome(chosen, unknown)
    by_source = {move.source: move for move in chosen}
    finder = Glossary({move.source: move.after for move in chosen})
    changed: list[tuple[str, str]] = []
    for text, target in list(memory.items()):
        if text in by_source:
            changed.append((text, by_source[text].after))
            continue
        found = finder.within(text, len(by_source))
        if not found:
            continue
        fresh = rewritten(
            target,
            (by_source[source] for source in found if by_source[source].swappable),
        )
        if fresh != target:
            changed.append((text, fresh))
            outcome.rewritten += 1
        if finder.unkept(text, fresh, len(by_source)) is not None:
            outcome.unresolved.append(text)
    memory.rewrite(changed)
    return outcome
