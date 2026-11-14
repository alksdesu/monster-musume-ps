from dataclasses import dataclass
from typing import TYPE_CHECKING

from .sessions import Session

if TYPE_CHECKING:
    from ..runtime import Runtime


@dataclass
class Context:
    runtime: "Runtime"
    payload: dict
    state: dict | None
    session: Session | None
    now: int
