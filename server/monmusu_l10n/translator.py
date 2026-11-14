import asyncio
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import groupby

from .batches import Batch, packed, waves
from .brief import Brief
from .catalog import Entry
from .glossary import Glossary, is_term
from .markup import fit, problem
from .memory import Memory
from .scenes import Line, Scene
from .service import Chat, Refused, Unavailable


NARRATION, CHOICE = "旁白", "选项"
# These rules are what the checks below hold a translation to; the two change together.
RULES = f"""输入是一个 JSON 对象：context 说明这批文本出自游戏的哪里，glossary 是必须沿用的译名，examples（如果有）是同一处已经定稿的原文与译文，texts 是编号到日文原文的映射，faults（如果有）是上一次译文不合格的原因。
剧情对话还带有 title、speakers 和 before：title 是这场剧情的标题；texts 按编号前后相接，是同一场戏里的对话；speakers 把每个编号映射到说这句话的人，「{NARRATION}」是叙述文字，「{CHOICE}」是给玩家点选的选项；before 是紧接在 texts 之前的几段对话和它们已有的译文，只用来把握情节、语气和人物之间的称呼，不要翻译它。
输出一个 JSON 对象，把每个编号映射到它的简体中文译文。编号与 texts 一一对应，不多不少，不要任何解释。

1. 尖括号标签（<color=yellow>、<size=30>、<link="火傷">、</color> 这类）原样保留，一个不能少，放在译文里对应的位置；标签里的属性值即使是日文也一个字都不要改。唯一的例外：<ruby=读音>词</ruby> 是给词标注读音的，译文里去掉这一对标签，只写这个词的中文。
2. 以英文字母或数字开头的方括号指令（[MAG,0,0]、[duration]、[cost]、[awaked,+10%] 这类）和花括号占位（{{0}}、{{0:f0}}）原样保留。唯一的例外：[awaked,…]、[notawaked,…] 这类指令里逗号后面如果是日文，那是显示给玩家的文字，要译成中文；方括号、开头的英文单词和逗号保持不变。
3. 换行符的数量与原文一致，逐行对应着翻译。
4. glossary 里的词只要在原文里出现，译文里就必须出现它给定的译名，一字不改。人名、种族名、技能名译成中文，不保留假名。
5. 【】「」和半角方括号 [ ] 都照原样保留，不要互相替换；数字、全角符号也照原样保留。
6. 句式、用词、标点与 examples 保持一致，同一种说法前后只用一种译法。
7. 对话要像这个人当面说出口的话：每个人的语气、口癖、自称和对别人的称呼与 before 里保持一致；喘息、呻吟和拟声词写成中文里自然的说法，不保留假名。glossary 里的人名偶尔只是同形的普通词（比如把水果叫作メロン），这时按普通词翻译。"""
GLOSSARY_ROOM = 60
EXAMPLES = 8
BEFORE = 12
ALONE = 2
EVERY = 15


@dataclass(frozen=True)
class Lexicon:
    names: Glossary
    hints: Glossary
    cast: Glossary


def lexicon(entries: Iterable[Entry], memory: Memory, brief: Brief) -> Lexicon:
    names: dict[str, str] = {}
    hints: dict[str, str] = {}
    cast: dict[str, str] = {}
    for entry in entries:
        target = memory.get(entry.text)
        if target is None or not is_term(entry.text):
            continue
        kind = names if brief.named(entry.origins) else hints
        kind[entry.text] = target
        if brief.in_cast(entry.origins):
            cast[entry.text] = target
    names.update(brief.terms)
    cast.update(brief.proper)
    # A translation is held to the names; other short texts are only wording to follow.
    return Lexicon(Glossary(names), Glossary({**hints, **names}), Glossary(cast))


def fault(text: str, target: object, names: Glossary) -> str | None:
    return problem(text, target) or names.unkept(text, target, GLOSSARY_ROOM)


def settled(text: str, memory: Memory, brief: Brief, names: Glossary) -> bool:
    if text in brief.terms:
        return True
    target = memory.get(text)
    # A kept translation that no longer passes is made again.
    return target is not None and fault(text, target, names) is None


def present(scene: Scene, cast: Glossary, brief: Brief) -> Glossary:
    # Away from its owner a name may be a plain word, so only who is there is held.
    speakers = {line.speaker for line in scene.lines}
    return cast.only(speakers | brief.proper.keys())


def _first(lines: Iterable[Line]) -> dict[str, int]:
    places: dict[str, int] = {}
    for place, line in enumerate(lines):
        places.setdefault(line.text, place)
    return places


@dataclass(frozen=True)
class Failure:
    text: str
    origins: tuple[str, ...]
    problem: str


@dataclass
class Tally:
    wanted: int = 0
    translated: int = 0
    failures: list[Failure] = field(default_factory=list)


class Translator:
    def __init__(
        self,
        chat: Chat,
        memory: Memory,
        brief: Brief,
        catalog: Iterable[Entry],
        scenes: Iterable[Scene],
        workers: int,
        tell: Callable[[str], None] = print,
    ):
        self.chat = chat
        self.memory = memory
        self.brief = brief
        self.tell = tell
        self.tally = Tally()
        self._system = f"{brief.style}\n\n{RULES}"
        self._slots = asyncio.Semaphore(workers)
        # Names and their order come from everything the game shows, whatever part is asked for.
        self._catalog = list(catalog)
        self._waves = waves(self._catalog)
        self._origins = {entry.text: entry.origins for entry in self._catalog}
        self._fields: dict[str, list[str]] = {}
        for entry in self._catalog:
            self._fields.setdefault(entry.origin, []).append(entry.text)
        self._scenes = {scene.origin: scene for scene in scenes}
        # Where a line is first said tells who says it and what was said before.
        self._places = {
            origin: _first(scene.lines) for origin, scene in self._scenes.items()
        }
        self._lexicon = Lexicon(Glossary({}), Glossary({}), Glossary({}))
        self._present: dict[str, Glossary] = {}

    async def run(
        self, entries: Iterable[Entry], scenes: Iterable[Scene] = ()
    ) -> Tally:
        scenes = list(scenes)
        chosen = {entry.text for entry in entries}
        # Whoever speaks in a scene is named before the scene is told.
        chosen.update(line.speaker for scene in scenes for line in scene.lines)
        self._pin()
        reporting = asyncio.create_task(self._report())
        try:
            for number, wave in enumerate(self._waves, 1):
                # A name settled in one wave is what the next is held to.
                self._lexicon = lexicon(self._catalog, self.memory, self.brief)
                pending = [
                    entry
                    for entry in wave
                    if entry.text in chosen
                    and not self._settled(entry.text, entry.origin)
                ]
                self.tally.wanted += len(pending)
                self.tell(
                    f"wave {number} of {len(self._waves)}: {len(pending)} texts, "
                    f"{len(self._lexicon.names)} settled names"
                )
                fields = groupby(packed(pending), key=lambda batch: batch.origin)
                await asyncio.gather(
                    *(self._field(list(batches)) for _, batches in fields)
                )
            # Talk comes last, once everyone in it has a name.
            self._lexicon = lexicon(self._catalog, self.memory, self.brief)
            await self._told(scenes)
        finally:
            reporting.cancel()
        return self.tally

    def _held(self, origin: str) -> Glossary:
        # A menu word means something else when spoken, so talk keeps only who people are.
        return self._present.get(origin, self._lexicon.names)

    def _settled(self, text: str, origin: str) -> bool:
        return settled(text, self.memory, self.brief, self._held(origin))

    def _pin(self) -> None:
        terms = self.brief.terms
        pinned = [
            (entry.text, terms[entry.text])
            for entry in self._catalog
            if entry.text in terms
        ]
        self.memory.record(
            pair for pair in pinned if self.memory.get(pair[0]) != pair[1]
        )

    async def _field(self, batches: list[Batch]) -> None:
        # The first batch of a field sets the wording the rest are held to.
        first, *rest = batches
        await self._settle(first)
        await asyncio.gather(*(self._settle(batch) for batch in rest))

    async def _told(self, scenes: Iterable[Scene]) -> None:
        claimed: set[str] = set()
        staged: list[list[Batch]] = []
        for scene in scenes:
            self._present[scene.origin] = present(scene, self._lexicon.cast, self.brief)
            # A line said in several scenes is translated in the first that says it.
            pending = [
                text
                for text in self._places[scene.origin]
                if text not in claimed and not self._settled(text, scene.origin)
            ]
            claimed.update(pending)
            for text in pending:
                self._origins.setdefault(text, (scene.origin,))
            if pending:
                lines = (Entry(text, (scene.origin,)) for text in pending)
                staged.append(list(packed(lines)))
        self.tally.wanted += len(claimed)
        self.tell(
            f"scenes: {len(claimed)} lines in {len(staged)} scenes, "
            f"{len(self._lexicon.cast)} names held"
        )
        await asyncio.gather(*(self._scene(batches) for batches in staged))

    async def _scene(self, batches: list[Batch]) -> None:
        # Each stretch is read with what was said before it, so a scene is told in order.
        for batch in batches:
            await self._settle(batch)

    def _examples(self, batch: Batch) -> dict[str, str]:
        found: dict[str, str] = {}
        for text in self._fields[batch.origin]:
            if len(found) == EXAMPLES:
                break
            if text not in batch.texts and self._settled(text, batch.origin):
                found[text] = self.memory.get(text)
        return found

    def _glossary(self, batch: Batch) -> dict[str, str]:
        spoken = batch.origin in self._scenes
        hints = self._lexicon.cast if spoken else self._lexicon.hints
        found: dict[str, str] = {}
        for text in batch.texts:
            found.update(hints.within(text, GLOSSARY_ROOM))
            found.update(self._held(batch.origin).within(text, GLOSSARY_ROOM))
        return found

    def _speaker(self, line: Line) -> str:
        if line.choice:
            return CHOICE
        return self.memory.get(line.speaker) or line.speaker or NARRATION

    def _said(self, line: Line) -> dict[str, str]:
        said = {"speaker": self._speaker(line), "text": line.text}
        target = self.memory.get(line.text)
        if target is not None:
            said["translation"] = target
        return said

    def _staging(self, scene: Scene, numbered: Mapping[str, str]) -> dict:
        places = self._places[scene.origin]
        first = min(places[text] for text in numbered.values())
        staging: dict = {"title": self.memory.get(scene.title) or scene.title}
        staging["before"] = [
            self._said(line) for line in scene.lines[max(0, first - BEFORE) : first]
        ]
        staging["speakers"] = {
            number: self._speaker(scene.lines[places[text]])
            for number, text in numbered.items()
        }
        return staging

    async def _report(self) -> None:
        while True:
            await asyncio.sleep(EVERY)
            usage = self.chat.usage
            self.tell(
                f"translated {self.tally.translated}/{self.tally.wanted}, "
                f"failed {len(self.tally.failures)}, requests {usage.requests}, "
                f"tokens read {usage.read} written {usage.written}"
            )

    async def _settle(
        self, batch: Batch, faults: Mapping[str, str] = {}, alone: int = 0
    ) -> None:
        outcome = await self._attempt(batch, faults)
        good = [
            (text, target)
            for text, (target, _) in outcome.items()
            if target is not None
        ]
        bad = {
            text: found for text, (target, found) in outcome.items() if target is None
        }
        self.memory.record(good)
        self.tally.translated += len(good)
        if not bad:
            return
        if len(batch.texts) == 1:
            if alone + 1 == ALONE:
                self.tally.failures.extend(
                    Failure(text, self._origins[text], found)
                    for text, found in bad.items()
                )
                return
            await self._settle(batch, bad, alone + 1)
        elif len(bad) == len(batch.texts):
            # A batch that fails whole is halved until the text at fault stands alone.
            await asyncio.gather(*(self._settle(half, bad) for half in batch.halves()))
        else:
            await self._settle(batch.only(bad), bad)

    def _request(self, batch: Batch, numbered: Mapping[str, str]) -> dict:
        request = {
            "context": self.brief.context(batch.origin),
            "glossary": self._glossary(batch),
        }
        scene = self._scenes.get(batch.origin)
        if scene is None:
            request["examples"] = self._examples(batch)
        else:
            request.update(self._staging(scene, numbered))
        request["texts"] = numbered
        return request

    async def _attempt(
        self, batch: Batch, faults: Mapping[str, str]
    ) -> dict[str, tuple[str | None, str | None]]:
        numbered = {str(number): text for number, text in enumerate(batch.texts, 1)}
        request = self._request(batch, numbered)
        known = {
            number: faults[text] for number, text in numbered.items() if text in faults
        }
        if known:
            request["faults"] = known
        try:
            async with self._slots:
                reply = await self.chat.ask(
                    self._system, json.dumps(request, ensure_ascii=False)
                )
        except Refused as error:
            return dict.fromkeys(
                batch.texts, (None, f"the service refused it: {error}")
            )
        except Unavailable as error:
            return dict.fromkeys(
                batch.texts, (None, f"the service stayed unreachable: {error}")
            )
        if not reply.complete:
            return dict.fromkeys(batch.texts, (None, "the reply was cut short"))
        try:
            answers = json.loads(reply.content)
        except ValueError:
            answers = None
        if not isinstance(answers, dict):
            return dict.fromkeys(batch.texts, (None, "the reply was not a JSON object"))
        held = self._held(batch.origin)
        outcome = {}
        for number, text in numbered.items():
            target = answers.get(number)
            target = fit(text, target) if isinstance(target, str) else target
            found = fault(text, target, held)
            outcome[text] = (target, None) if found is None else (None, found)
        return outcome
