from copy import deepcopy
from dataclasses import dataclass
import secrets

from .sessions import Session


@dataclass
class Cursor:
    family: str
    rows: list
    per_view: int
    offset: int
    extra: dict


def start(
    session: Session, family: str, rows: list, per_view: int, extra: dict | None = None
) -> dict:
    if type(per_view) is not int or per_view < 1:
        raise ValueError("per_view must be a positive integer")
    cursor = Cursor(family, deepcopy(rows), per_view, 0, extra or {})
    return _read(session, cursor)


def resume(session: Session, family: str, token: str) -> dict:
    cursor = session.cursors[token]
    if cursor.family != family:
        raise ValueError("Sequence token belongs to another list")
    del session.cursors[token]
    return _read(session, cursor)


def _read(session: Session, cursor: Cursor) -> dict:
    rows = cursor.rows[cursor.offset : cursor.offset + cursor.per_view]
    cursor.offset += len(rows)
    token = None
    if cursor.offset < len(cursor.rows):
        token = secrets.token_urlsafe(24)
        session.cursors[token] = cursor
    return {"items": rows, "sequence_token": token, **cursor.extra}
