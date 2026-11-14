import asyncio
import json
import random
from dataclasses import dataclass

import aiohttp


ENDPOINT = "https://api.deepseek.com/chat/completions"
UNTHINKING = "none"
EFFORTS = (UNTHINKING, "low", "high", "max")
BUSY = (429, 500, 502, 503, 504)
BARRED = (401, 402, 403)
ATTEMPTS = 8
LONGEST_WAIT = 60
# The service keeps a queued request open instead of turning it away, so the wait is ours to end.
PATIENCE = 300
OUTPUT_ROOM = 32768


class Refused(Exception):
    pass


class Halted(Exception):
    pass


class Unavailable(Exception):
    pass


@dataclass(frozen=True)
class Reply:
    content: str
    complete: bool


@dataclass
class Usage:
    requests: int = 0
    read: int = 0
    cached: int = 0
    written: int = 0
    reasoned: int = 0

    def add(self, counted: dict) -> None:
        self.requests += 1
        self.read += counted.get("prompt_tokens", 0)
        self.cached += counted.get("prompt_cache_hit_tokens", 0)
        self.written += counted.get("completion_tokens", 0)
        self.reasoned += (counted.get("completion_tokens_details") or {}).get(
            "reasoning_tokens", 0
        )


class Chat:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        key: str,
        model: str,
        effort: str,
        endpoint: str = ENDPOINT,
        patience: float = PATIENCE,
    ):
        if effort not in EFFORTS:
            raise ValueError(f"thinking is one of {', '.join(EFFORTS)}")
        self.session = session
        self.model = model
        self.endpoint = endpoint
        self._patience = aiohttp.ClientTimeout(total=patience)
        self.usage = Usage()
        self._headers = {"Authorization": f"Bearer {key}"}
        self._thinking = (
            {"thinking": {"type": "disabled"}}
            if effort == UNTHINKING
            else {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
        )

    async def ask(self, system: str, user: str) -> Reply:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": OUTPUT_ROOM,
            **self._thinking,
        }
        failure = "no attempt was made"
        for attempt in range(ATTEMPTS):
            if attempt:
                # Jitter keeps hundreds of waiting requests from returning in one wave.
                await asyncio.sleep(
                    min(LONGEST_WAIT, 2**attempt) * (0.5 + random.random())
                )
            try:
                async with self.session.post(
                    self.endpoint,
                    json=body,
                    headers=self._headers,
                    timeout=self._patience,
                ) as response:
                    text = await response.text()
                    status = response.status
            except asyncio.TimeoutError:
                # Asking again for the same batch would wait as long; the caller splits it instead.
                raise Unavailable(
                    f"no answer within {self._patience.total:.0f} seconds"
                ) from None
            except aiohttp.ClientError as error:
                failure = f"{type(error).__name__}: {error}"
                continue
            if status in BARRED:
                raise Halted(
                    f"the service turned the key away ({status}): {text[:300]}"
                )
            if status in BUSY:
                failure = f"the service answered {status}"
                continue
            if status != 200:
                raise Refused(f"{status}: {text[:300]}")
            answer = json.loads(text)
            self.usage.add(answer.get("usage") or {})
            choice = answer["choices"][0]
            return Reply(
                choice["message"].get("content") or "",
                choice.get("finish_reason") == "stop",
            )
        raise Unavailable(failure)
