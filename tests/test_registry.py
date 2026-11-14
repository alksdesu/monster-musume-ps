import asyncio
import itertools
import logging
import os
import sys
import time
from types import SimpleNamespace

import pytest

from monmusu_ps.core.context import Context
from monmusu_ps.core.registry import FeatureRegistry
from monmusu_ps.core.sessions import Sessions
from monmusu_ps.core.store import PlayerStore
import monmusu_ps.features as features


@pytest.fixture
def write_feature(tmp_path):
    timestamps = itertools.count(time.time_ns(), 1_000_000)

    def write(name, source):
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        timestamp = next(timestamps)
        os.utime(path, ns=(timestamp, timestamp))
        return path

    return write


@pytest.fixture
def registry_factory(tmp_path):
    registries = []

    def create(contracts=None):
        registry = FeatureRegistry(
            tmp_path, contracts or {"alpha": {}, "beta": {}, "gamma": {}}
        )
        registries.append(registry)
        return registry

    yield create
    for registry in registries:
        registry.close()


def feature(endpoint, value):
    return f"VALUE = {value!r}\ndef handle(ctx):\n    return VALUE\nROUTES = {{{endpoint!r}: handle}}\n"


def revision_modules():
    return {
        name for name in sys.modules if name.startswith("monmusu_ps.features._reload_")
    }


def test_new_handlers_have_separate_globals_and_unchanged_refresh_keeps_identity(
    write_feature, registry_factory
):
    write_feature("alpha.py", feature("alpha", "old"))
    registry = registry_factory()
    old_mapping = registry.handlers
    old = old_mapping["alpha"]
    registry.refresh()
    assert registry.handlers is old_mapping
    write_feature("alpha.py", feature("alpha", "new"))
    registry.refresh()
    current = registry.handlers["alpha"]
    assert current(None) == "new"
    assert old(None) == "old"
    assert current.__globals__ is not old.__globals__
    current.__globals__["VALUE"] = "changed"
    assert old(None) == "old"
    with pytest.raises(TypeError):
        registry.handlers["alpha"] = old


def test_syntax_failure_keeps_the_entire_previous_table(
    write_feature, registry_factory, caplog
):
    write_feature("alpha.py", feature("alpha", "alpha-old"))
    write_feature("beta.py", feature("beta", "beta-old"))
    registry = registry_factory()
    previous = registry.handlers
    names = revision_modules()
    write_feature("alpha.py", feature("alpha", "alpha-new"))
    write_feature("beta.py", "def broken(:\n")
    with caplog.at_level(logging.ERROR, logger="monmusu.features"):
        registry.refresh()
        registry.refresh()
    assert registry.handlers is previous
    assert registry.handlers["alpha"](None) == "alpha-old"
    assert registry.handlers["beta"](None) == "beta-old"
    assert revision_modules() == names
    assert len(caplog.records) == 1
    assert "SyntaxError" in caplog.text
    write_feature("beta.py", feature("beta", "beta-new"))
    registry.refresh()
    assert registry.handlers["alpha"](None) == "alpha-new"
    assert registry.handlers["beta"](None) == "beta-new"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("ROUTES = {'unlisted': lambda ctx: None}\n", "unknown endpoint"),
        ("ROUTES = {'alpha': lambda ctx: None}\n", "duplicate endpoint"),
        ("ROUTES = {'beta': 42}\n", "must be callable"),
        ("ROUTES = []\n", "must be a mapping"),
        ("VALUE = 1\n", "must be a mapping"),
        (
            "raise RuntimeError('module initialization failed')\n",
            "module initialization failed",
        ),
    ],
)
def test_invalid_registration_keeps_previous_version(
    write_feature, registry_factory, caplog, source, message
):
    write_feature("alpha.py", feature("alpha", "active"))
    registry = registry_factory()
    previous = registry.handlers
    names = revision_modules()
    write_feature("beta.py", source)
    with caplog.at_level(logging.ERROR, logger="monmusu.features"):
        registry.refresh()
    assert registry.handlers is previous
    assert previous["alpha"](None) == "active"
    assert revision_modules() == names
    assert message in caplog.text


def test_add_and_delete_modules_without_revision_leaks(write_feature, registry_factory):
    baseline = revision_modules()
    first_path = write_feature("alpha.py", feature("alpha", 1))
    registry = registry_factory()
    old = registry.handlers["alpha"]
    first_revision = revision_modules() - baseline
    second_path = write_feature("beta.py", feature("beta", 2))
    registry.refresh()
    assert set(registry.handlers) == {"alpha", "beta"}
    assert not first_revision & revision_modules()
    assert len(revision_modules() - baseline) == 2
    first_path.unlink()
    registry.refresh()
    assert set(registry.handlers) == {"beta"}
    assert old(None) == 1
    assert len(revision_modules() - baseline) == 1
    second_path.unlink()
    registry.refresh()
    assert dict(registry.handlers) == {}
    assert revision_modules() == baseline


def test_size_change_is_detected_even_with_the_same_mtime(
    write_feature, registry_factory
):
    path = write_feature("alpha.py", feature("alpha", "a"))
    registry = registry_factory()
    stamp = path.stat().st_mtime_ns
    path.write_text(feature("alpha", "longer-value"), encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))
    registry.refresh()
    assert registry.handlers["alpha"](None) == "longer-value"


@pytest.mark.parametrize(
    ("source", "error"),
    [
        ("def broken(:\n", SyntaxError),
        ("ROUTES = {'unlisted': lambda ctx: None}\n", ValueError),
    ],
)
def test_initial_load_failure_raises_and_cleans_modules(
    tmp_path, write_feature, source, error
):
    baseline = revision_modules()
    write_feature("alpha.py", feature("alpha", "candidate"))
    write_feature("beta.py", source)
    with pytest.raises(error):
        FeatureRegistry(tmp_path, {"alpha": {}})
    assert revision_modules() == baseline


def test_missing_feature_directory_raises_at_startup(tmp_path):
    with pytest.raises(FileNotFoundError):
        FeatureRegistry(tmp_path / "missing", {})


def test_relative_core_and_helper_imports(
    write_feature, registry_factory, monkeypatch, tmp_path
):
    helper = "_registry_test_helper"
    write_feature(helper + ".py", "def label():\n    return 'helper'\n")
    write_feature("_unused.py", "def ignored(:\n")
    write_feature(
        "alpha.py",
        (
            "from ..core.context import Context\n"
            f"from .{helper} import label\n"
            "def handle(ctx):\n    return isinstance(ctx, Context), label()\n"
            "ROUTES = {'alpha': handle}\n"
        ),
    )
    monkeypatch.setattr(features, "__path__", [str(tmp_path)])
    try:
        registry = registry_factory()
        assert registry.handlers["alpha"](Context(None, {}, {}, None, 0)) == (
            True,
            "helper",
        )
        assert set(registry.handlers) == {"alpha"}
    finally:
        sys.modules.pop(f"monmusu_ps.features.{helper}", None)
        vars(features).pop(helper, None)


async def test_in_flight_async_handler_and_core_state_survive_reload(
    write_feature, registry_factory, tmp_path
):
    source = (
        "from ..core.context import Context\n"
        "VALUE = 'old'\n"
        "async def handle(ctx):\n"
        "    ctx.payload['entered'].set()\n"
        "    await ctx.payload['release'].wait()\n"
        "    async with ctx.runtime.store.transaction(ctx.session.profile) as state:\n"
        "        state['calls'].append(VALUE)\n"
        "    return VALUE, ctx.runtime.store, ctx.runtime.sessions, ctx.session\n"
        "ROUTES = {'alpha': handle}\n"
    )
    write_feature("alpha.py", source)
    registry = registry_factory()
    store = PlayerStore(tmp_path / "saves", lambda profile: {"calls": []})
    sessions = Sessions()
    session = sessions.create("local")
    entered, release = asyncio.Event(), asyncio.Event()
    context = Context(
        SimpleNamespace(store=store, sessions=sessions),
        {"entered": entered, "release": release},
        None,
        session,
        0,
    )
    running = asyncio.create_task(registry.handlers["alpha"](context))
    await entered.wait()
    write_feature("alpha.py", source.replace("VALUE = 'old'", "VALUE = 'new'"))
    registry.refresh()
    release.set()
    old_result = await running
    new_result = await registry.handlers["alpha"](context)
    assert old_result == ("old", store, sessions, session)
    assert new_result == ("new", store, sessions, session)
    assert (await store.read("local"))["calls"] == ["old", "new"]
    assert sessions.get(session.token) is session


def test_close_removes_current_revision_modules(write_feature, registry_factory):
    baseline = revision_modules()
    write_feature("alpha.py", feature("alpha", 1))
    registry = registry_factory()
    handler = registry.handlers["alpha"]
    assert len(revision_modules() - baseline) == 1
    registry.close()
    assert revision_modules() == baseline
    assert handler(None) == 1
