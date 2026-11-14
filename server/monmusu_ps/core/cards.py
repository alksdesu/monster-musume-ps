import json

from .errors import ApiNotFound


def text(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


# Rarity ids are the big-endian bytes of the rarity name.
def rarity_id(name: str) -> int:
    return int.from_bytes(name.encode("ascii"), "big")


def _find(rows: list[dict], kind: str, instance_id) -> dict:
    row = next((row for row in rows if row["instance_id"] == instance_id), None)
    if row is None:
        raise ApiNotFound(f"{kind}.{instance_id}")
    return row


def find_unit(state: dict, instance_id) -> dict:
    return _find(state["units"], "unit", instance_id)


def find_bom(state: dict, instance_id) -> dict:
    return _find(state["boms"], "bom", instance_id)
