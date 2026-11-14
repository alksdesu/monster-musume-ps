import asyncio
from collections import deque
import logging
import time

from aiohttp import web


RESTART_CODE = 75
LEAVE_DELAY = 0.3


class LogRing(logging.Handler):
    def __init__(self, capacity: int):
        super().__init__()
        self._records: deque[dict] = deque(maxlen=capacity)
        self._sequence = 0

    def emit(self, record: logging.LogRecord) -> None:
        self._sequence += 1
        self._records.append(
            {
                "seq": self._sequence,
                "time": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )

    @property
    def latest(self) -> int:
        return self._sequence

    def after(self, sequence: int) -> list[dict]:
        return [row for row in self._records if row["seq"] > sequence]


class Service:
    def __init__(self, runtime, ring: LogRing):
        self.runtime = runtime
        self.ring = ring
        self.started_at = int(time.time())
        self.paused = False
        self.exit_code = 0

    def status(self) -> dict:
        runtime = self.runtime
        runtime.registry.refresh()
        runtime.overrides.reload()
        runtime.schedule.reload()
        return {
            "paused": self.paused,
            "started_at": self.started_at,
            "now": int(runtime.clock()),
            "endpoints": len(runtime.registry.handlers),
            "contracts": len(runtime.contracts),
            "online": [session.profile for session in runtime.sessions.active()],
            "schema_version": runtime.store.schema_version,
            "problems": {
                "features": runtime.registry.error,
                "settings": runtime.config.error,
                "schedule": runtime.schedule.config.error or runtime.schedule.error,
                "overrides": runtime.overrides.report(),
            },
        }

    def pause(self, paused: bool) -> None:
        self.paused = paused

    def leave(self, restart: bool) -> None:
        self.exit_code = RESTART_CODE if restart else 0
        # The answer to this request has to reach the browser first.
        asyncio.get_running_loop().call_later(LEAVE_DELAY, self._leave)

    @staticmethod
    def _leave() -> None:
        raise web.GracefulExit()
