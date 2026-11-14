import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

import aiohttp

from . import catalog, renaming, scenes
from .batches import waves
from .brief import Brief
from .memory import Memory
from .service import EFFORTS, Chat, Halted
from .translator import Translator, lexicon, present, settled


MASTERS = "masterdata/assets/resources/mdf_output"
SCENARIOS = "scenarios"
WORKSPACE = "localization"
SOURCE = "source.jsonl"
SCENES = "scenes.jsonl"
BRIEF = "brief.json"
MEMORY = "zh-Hans.jsonl"
REPORT = "report.json"
KEY = "DEEPSEEK_API_KEY"


def extract(arguments) -> None:
    masters = arguments.root / MASTERS
    told = scenes.collect(arguments.root / SCENARIOS, masters / catalog.TABLES)
    entries = catalog.collect(masters, scenes.names(told))
    catalog.save(entries, arguments.workspace / SOURCE)
    scenes.save(told, arguments.workspace / SCENES)
    places = {origin for entry in entries for origin in entry.origins}
    characters = sum(len(entry.text) for entry in entries)
    print(
        f"{len(entries)} texts, {characters} characters, from {len(places)} fields -> {arguments.workspace / SOURCE}"
    )
    lines = [line.text for scene in told for line in scene.lines]
    print(
        f"{len(told)} scenes, {len(lines)} lines, {sum(map(len, lines))} characters -> {arguments.workspace / SCENES}"
    )


def chosen[Item: (catalog.Entry, scenes.Scene)](
    items: list[Item], arguments
) -> list[Item]:
    if arguments.only:
        items = [
            item for item in items if item.origin.startswith(tuple(arguments.only))
        ]
    if arguments.sample and len(items) > arguments.sample:
        # Evenly spaced picks reach every table instead of the first one alone.
        items = items[:: len(items) // arguments.sample][: arguments.sample]
    return items


async def translating(arguments) -> int:
    key = os.environ.get(KEY)
    if not key:
        print(f"set {KEY} to the key of the translation service")
        return 2
    entries = catalog.load(arguments.workspace / SOURCE)
    told = scenes.load(arguments.workspace / SCENES)
    brief = Brief.load(arguments.workspace / BRIEF)
    connector = aiohttp.TCPConnector(limit=arguments.workers)
    async with aiohttp.ClientSession(connector=connector) as session:
        chat = Chat(session, key, arguments.model, arguments.thinking)
        with Memory(arguments.memory) as memory:
            translator = Translator(
                chat, memory, brief, entries, told, arguments.workers
            )
            try:
                tally = await translator.run(
                    chosen(entries, arguments), chosen(told, arguments)
                )
            except Halted as error:
                print(f"stopped: {error}")
                return 1
    report = {
        "model": arguments.model,
        "thinking": arguments.thinking,
        "wanted": tally.wanted,
        "translated": tally.translated,
        "usage": asdict(chat.usage),
        "failures": [asdict(failure) for failure in tally.failures],
    }
    with arguments.report.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, ensure_ascii=False, indent=1)
    print(
        f"translated {tally.translated} of {tally.wanted}, failed {len(tally.failures)}; "
        f"{chat.usage.requests} requests, tokens read {chat.usage.read} (cached {chat.usage.cached}) "
        f"written {chat.usage.written} (reasoning {chat.usage.reasoned}) -> {arguments.report}"
    )
    return 0


def translate(arguments) -> None:
    raise SystemExit(asyncio.run(translating(arguments)))


def status(arguments) -> None:
    entries = catalog.load(arguments.workspace / SOURCE)
    memory = Memory(arguments.memory)
    brief = Brief.load(arguments.workspace / BRIEF)
    held = lexicon(entries, memory, brief)
    names = held.names
    done = 0
    for number, wave in enumerate(waves(entries), 1):
        passing = sum(settled(entry.text, memory, brief, names) for entry in wave)
        done += passing
        print(f"wave {number}: {passing}/{len(wave)} translated")
    kept = sum(entry.text in memory for entry in entries)
    print(f"all: {done}/{len(entries)}, {kept - done} kept but failing the checks")
    told = scenes.load(arguments.workspace / SCENES)
    lines: dict[str, bool] = {}
    for scene in told:
        there = present(scene, held.cast, brief)
        for line in scene.lines:
            if line.text not in lines:
                lines[line.text] = settled(line.text, memory, brief, there)
    print(
        f"scenes: {sum(lines.values())}/{len(lines)} lines translated in {len(told)} scenes"
    )


def rename(arguments) -> None:
    offered = json.loads(arguments.names.read_text(encoding="utf-8-sig"))
    if not isinstance(offered, dict) or not all(
        isinstance(value, str) for value in offered.values()
    ):
        raise SystemExit("the names file has to map source names to their renderings")
    entries = catalog.load(arguments.workspace / SOURCE)
    memory = Memory(arguments.memory)
    brief = Brief.load(arguments.workspace / BRIEF)
    outcome = renaming.apply(offered, entries, memory, brief)
    for move in outcome.moves:
        print(f"{move.source}: {move.before} -> {move.after}")
    print(
        f"renamed {len(outcome.moves)} names, rewrote {outcome.rewritten} texts, "
        f"{len(outcome.unresolved)} texts still without the new rendering, "
        f"{len(outcome.unknown)} offered names outside the cast -> {arguments.memory}"
    )
    for text in outcome.unresolved[:20]:
        print("unresolved:", json.dumps(text, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(prog="monmusu_l10n")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--workspace", type=Path)
    commands = parser.add_subparsers(required=True)
    commands.add_parser("extract").set_defaults(run=extract)
    translation = commands.add_parser("translate")
    translation.add_argument("--model", default="deepseek-flash")
    translation.add_argument("--thinking", choices=EFFORTS, default="high")
    translation.add_argument("--workers", type=int, default=300)
    translation.add_argument("--sample", type=int)
    translation.add_argument("--only", action="append")
    translation.add_argument("--memory", type=Path)
    translation.add_argument("--report", type=Path)
    translation.set_defaults(run=translate)
    progress = commands.add_parser("status")
    progress.add_argument("--memory", type=Path)
    progress.set_defaults(run=status)
    renamer = commands.add_parser("rename")
    renamer.add_argument("--names", type=Path, required=True)
    renamer.add_argument("--memory", type=Path)
    renamer.set_defaults(run=rename)
    arguments = parser.parse_args()
    arguments.workspace = arguments.workspace or arguments.root / WORKSPACE
    if "memory" in arguments:
        arguments.memory = arguments.memory or arguments.workspace / MEMORY
    if "report" in arguments:
        arguments.report = arguments.report or arguments.workspace / REPORT
    arguments.run(arguments)
