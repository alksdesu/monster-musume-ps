"""按 masterdata 里的剧情表、关卡表和 ablist 拉取剧情脚本包，导出到 scenarios/；--missing 只补还没有的。"""

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

from fetch_master import CDN, PLATFORM, export_bundle

ROOT = os.path.join(os.path.dirname(__file__), "..")
MASTER = os.path.join(ROOT, "masterdata")
TABLES = os.path.join(MASTER, "assets", "resources", "mdf_output", "data")
OUT = os.path.join(ROOT, "scenarios")
STORY_TABLES = (
    "story_data_unit",
    "story_data_main",
    "story_data_other",
    "story_data_event",
    "story_data_group",
)
# 关卡战斗前后的剧情只记在关卡表里
QUEST_TABLE = "quest_data"
QUEST_FIELDS = (
    "before_story_chapter_id",
    "bfr_btl_story_chapter_id",
    "afr_btl_story_chapter_id",
    "after_story_chapter_id",
)
CHAPTER_INDEX = "ScenarioChapter/config.chapter"
EXPORTED = ".book.json"
ONLY_MISSING = "--missing"
WORKERS = 16
RETRIES = 3


def bundle_name(logical: str) -> str:
    return (
        hashlib.md5(logical.lower().replace("/", "-").encode()).hexdigest() + ".bytes"
    )


def scenario_names() -> list[str]:
    names = set()
    for table in STORY_TABLES:
        with open(os.path.join(TABLES, f"{table}.json"), encoding="utf-8") as f:
            names.update(row["scenario_name"] for row in json.load(f)["table"])
    with open(os.path.join(TABLES, f"{QUEST_TABLE}.json"), encoding="utf-8") as f:
        for row in json.load(f)["table"]:
            names.update(row[field] for field in QUEST_FIELDS if row.get(field))
    return sorted(names)


def on_disk() -> set[str]:
    found = set()
    for _, _, files in os.walk(OUT):
        found.update(f.removesuffix(EXPORTED) for f in files if f.endswith(EXPORTED))
    return found


def fetch(base_url: str, asset: dict) -> list[str]:
    url = f"{base_url}/{asset['hash']}{asset['path']}"
    for attempt in range(RETRIES):
        try:
            blob = requests.get(url, timeout=60).content
            if len(blob) != int(asset["size"]):
                raise ValueError(f"size {len(blob)} != {asset['size']}")
            return export_bundle(blob, OUT)
        except (requests.RequestException, ValueError):
            if attempt + 1 == RETRIES:
                raise
    return []


def main():
    # 脚本名取自本地主数据，所以用同一次拉取的 ablist，不重新协商版本
    with open(os.path.join(MASTER, "ablist.json"), encoding="utf-8") as f:
        ablist = json.load(f)
    assets = {a["path"]: a for a in ablist["data"]}
    base_url = f"{CDN}/ver_{ablist['baseVersion']}/{PLATFORM}"

    names = scenario_names()
    if ONLY_MISSING in sys.argv:
        names = sorted(set(names) - on_disk())
    wanted = {name: bundle_name(f"ScenarioChapter/{name}.book") for name in names}
    wanted[CHAPTER_INDEX] = bundle_name(CHAPTER_INDEX)
    absent = sorted(name for name, path in wanted.items() if path not in assets)
    listed = [assets[path] for path in wanted.values() if path in assets]
    size = sum(int(a["size"]) for a in listed)
    print(
        f"baseVersion: {ablist['baseVersion']} scripts: {len(listed)} ({size / 1048576:.1f} MB) not listed: {absent}"
    )

    os.makedirs(OUT, exist_ok=True)
    count, failed = 0, []
    with ThreadPoolExecutor(WORKERS) as pool:
        jobs = {pool.submit(fetch, base_url, a): a for a in listed}
        for done, job in enumerate(jobs, 1):
            try:
                count += len(job.result())
            except Exception:
                failed.append(jobs[job])
            if done % 500 == 0:
                print(f"{done}/{len(jobs)}")
    # 并发时偶发握手失败，剩下的逐个再试一遍
    lost = []
    for asset in failed:
        try:
            count += len(fetch(base_url, asset))
        except Exception as error:
            lost.append((asset["path"], repr(error)[:120]))
    print(f"exported: {count} retried: {len(failed)} failed: {len(lost)} {lost[:5]}")


if __name__ == "__main__":
    main()
