import asyncio
import subprocess


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STOPPED, STARTING, RUNNING, STOPPING = "stopped", "starting", "running", "stopping"


class Failure(Exception):
    def __init__(self, code: str, message: str, log: str = ""):
        super().__init__(message)
        self.code = code
        self.log = log

    def payload(self) -> dict:
        return {"code": self.code, "error": str(self), "log": self.log}


class Supervised:
    def __init__(self):
        self.failure: dict | None = None
        self._phase: str | None = None
        self._lock = asyncio.Lock()

    async def _run(self, *steps) -> None:
        async with self._lock:
            self.failure = None
            try:
                for step in steps:
                    await step()
            except Failure as error:
                self.failure = error.payload()
                raise
