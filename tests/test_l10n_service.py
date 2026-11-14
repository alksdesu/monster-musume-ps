import asyncio

import aiohttp
import pytest
from aiohttp import web

from monmusu_l10n import service
from monmusu_l10n.service import Chat, Halted, Refused, Reply, Unavailable


ANSWER = {
    "choices": [
        {
            "message": {"content": '{"1": "琳"}', "reasoning_content": "…"},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 90,
        "prompt_cache_hit_tokens": 64,
        "completion_tokens": 31,
        "completion_tokens_details": {"reasoning_tokens": 20},
    },
}


@pytest.fixture(autouse=True)
def unhurried(monkeypatch):
    waited = []

    async def wait(seconds):
        waited.append(seconds)

    monkeypatch.setattr(service.asyncio, "sleep", wait)
    return waited


@pytest.fixture
async def served():
    replies, asked = [], []
    released = asyncio.Event()

    async def handle(request):
        asked.append((request.headers["Authorization"], await request.json()))
        reply = replies.pop(0)
        if reply is None:
            await released.wait()
            return web.Response(status=503)
        status, body = reply
        return web.json_response(body, status=status)

    application = web.Application()
    application.router.add_post("/chat/completions", handle)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as session:
        yield session, f"http://127.0.0.1:{port}/chat/completions", replies, asked
    # A handler still waiting would hold the shutdown for a minute.
    released.set()
    await runner.cleanup()


def chat(served, effort="high", patience=5.0) -> Chat:
    session, endpoint, _, _ = served
    return Chat(session, "key", "model", effort, endpoint, patience)


async def test_an_answer_is_read_with_what_it_cost(served):
    served[2].append((200, ANSWER))
    asking = chat(served)
    assert await asking.ask("system", "user") == Reply('{"1": "琳"}', True)
    key, body = served[3][0]
    assert key == "Bearer key"
    assert body["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert (body["thinking"], body["reasoning_effort"], body["response_format"]) == (
        {"type": "enabled"},
        "high",
        {"type": "json_object"},
    )
    usage = asking.usage
    assert (
        usage.requests,
        usage.read,
        usage.cached,
        usage.written,
        usage.reasoned,
    ) == (1, 90, 64, 31, 20)


async def test_thinking_can_be_switched_off(served):
    served[2].append((200, ANSWER))
    await chat(served, "none").ask("system", "user")
    body = served[3][0][1]
    assert (body["thinking"], "reasoning_effort" in body) == (
        {"type": "disabled"},
        False,
    )
    with pytest.raises(ValueError):
        chat(served, "extreme")


async def test_an_answer_cut_short_is_told_apart(served):
    cut = {
        **ANSWER,
        "choices": [{"message": {"content": '{"1": "'}, "finish_reason": "length"}],
    }
    served[2].append((200, cut))
    assert await chat(served).ask("system", "user") == Reply('{"1": "', False)


async def test_a_busy_service_is_asked_again(served, unhurried):
    served[2].extend([(429, {}), (503, {}), (200, ANSWER)])
    assert (await chat(served).ask("system", "user")).complete
    assert (len(served[3]), len(unhurried)) == (3, 2)


async def test_a_service_that_stays_busy_is_given_up_on(served):
    served[2].extend([(503, {})] * service.ATTEMPTS)
    with pytest.raises(Unavailable, match="503"):
        await chat(served).ask("system", "user")
    assert len(served[3]) == service.ATTEMPTS


async def test_a_refusal_is_not_asked_again(served):
    served[2].append((400, {"error": {"message": "Content Exists Risk"}}))
    with pytest.raises(Refused, match="Content Exists Risk"):
        await chat(served).ask("system", "user")
    assert len(served[3]) == 1


async def test_a_key_that_is_turned_away_stops_everything(served):
    served[2].append((402, {"error": {"message": "Insufficient Balance"}}))
    with pytest.raises(Halted, match="Insufficient Balance"):
        await chat(served).ask("system", "user")


async def test_a_request_left_hanging_is_handed_back(served):
    served[2].append(None)
    with pytest.raises(Unavailable, match="no answer within 0 seconds"):
        await chat(served, patience=0.2).ask("system", "user")
    assert len(served[3]) == 1
