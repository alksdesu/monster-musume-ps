import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import pytest


PROJECT = Path(__file__).resolve().parents[1]
BINARY = PROJECT.parent / "mnms_td_r18/GameAssembly.dll"
DUMP = PROJECT / "decompile/il2cpp_dump/dump.cs"

# The client binary, its IL2CPP dump and the captures stay on the machine that made them.
if not (BINARY.exists() and DUMP.exists() and (PROJECT / "captures/digest").exists()):
    pytest.skip("needs the client binary, its dump and the captures", allow_module_level=True)
contracts = pytest.importorskip("tools.reverse.dump_request_contracts")
ConstructorReader, generate, parse_types = (
    contracts.ConstructorReader,
    contracts.generate,
    contracts.parse_types,
)


@pytest.fixture(scope="module")
def generated():
    return generate(PROJECT, BINARY)


def test_generated_artifacts_match_client(generated):
    for filename, actual in zip(
        ["contracts.json", "api_schema.json", "endpoints.json"], generated, strict=True
    ):
        saved = json.loads(
            (PROJECT / "protocol" / filename).read_text(encoding="utf-8")
        )
        assert actual == saved


def test_complete_route_inventory(generated):
    contracts, schema, endpoints = generated
    routes = contracts["routes"]
    primary = [route for route in routes if route["family"] == "ntd"]
    assert len(primary) == 163
    assert Counter(route["method"] for route in primary) == {"POST": 153, "PUT": 10}
    assert Counter(route["protocol"] for route in primary) == {1: 3, 2: 153, 3: 7}
    assert all(route["auth"] == (route["protocol"] == 2) for route in primary)
    assert schema["stats"] == {"services": 148, "models": 3736, "endpoints": 165}
    assert len(endpoints) == len(set(endpoints)) == len(routes)
    assert {route["endpoint"] for route in routes if route["family"] == "legacy"} == {
        "gha/wglopt/get",
        "gha/wglopt/set",
    }
    for route in routes:
        if route["family"] == "legacy":
            assert route["method"] is route["protocol"] is route["auth"] is None
            assert route["response_models"] == {}


@pytest.mark.parametrize(
    ("service", "endpoint"),
    [
        ("CheckMaintenanceRequestService", "maintenance/check"),
        ("DrawGachaRequestService", "gacha/draw"),
        ("FinishQuestRequestService", "quest/end"),
        ("SkipQuestRequestService", "quest/cycle_exec"),
        ("InitializePlayerRequestService", "player/init"),
        ("GetUnitsRequestService", "unit/all"),
        ("GetPrefsRequestService", "prefs/list"),
        ("ReceiveMissionRewardRequestService", "mission/receive"),
        ("UpdateTeamRequestService", "composition/update"),
        ("ReinforceUniqueWeaponRequestService", "unit/enhancement/uniqueWeapon"),
        ("UpdatePlayerNameRequestService", "player/nickname/update/"),
        ("GetBattleLogRequestService", "battle_log/"),
    ],
)
def test_service_routes_follow_request_inheritance(generated, service, endpoint):
    assert generated[1]["services"][service]["endpoint"] == endpoint


def test_wire_models_and_inherited_fields(generated):
    contracts, schema, _ = generated
    routes = {route["endpoint"]: route for route in contracts["routes"]}
    models = schema["models"]
    assert models["NtdLoginByDmmGamePlayerRequest"]["fields"] == []
    assert [
        field["name"]
        for field in models["NtdLoginByDmmGamePlayerRequest"]["effective_fields"]
    ] == ["id", "ot", "ap", "cvr", "ab"]
    assert models["AppEndpointConfigRequestService"]["base"] == (
        "ServerRequestService<NtdAppEndpointConfigRequest, AppEndpointConfigResponse>"
    )
    envelope = ["status", "sync", "log", "timesync", "to"]
    for endpoint in ["player/init", "tutorial/skip", "player/nickname/update/"]:
        name = routes[endpoint]["response_models"]["200"]
        assert [field["name"] for field in models[name]["effective_fields"]] == envelope
    assert "data" in [
        field["name"]
        for field in models[routes["prefs/set"]["response_models"]["200"]][
            "effective_fields"
        ]
    ]
    assert (
        routes["auth/login/dmm_gp"]["response_models"]["422"]
        == "LoginByDmmGamePlayerResponse422"
    )
    for route in routes.values():
        for name in route["response_models"].values():
            assert name in models
        for service in route["services"]:
            assert (
                schema["services"][service["name"]]["request_class"]
                == route["request_class"]
            )


def test_captured_request_protocols_match_native_contracts(generated):
    routes = {route["endpoint"]: route for route in generated[0]["routes"]}
    observed = set()
    for path in sorted((PROJECT / "captures").glob("session_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["kind"] != "req":
                continue
            request = record["data"]
            endpoint = urlsplit(request["url"]).path.removeprefix("/api/")
            assert routes[endpoint]["protocol"] == request["proto"]
            observed.add(endpoint)
    baseline = json.loads(
        (PROJECT / "captures/digest/_matrix.json").read_text(encoding="utf-8")
    )
    assert {row["endpoint"] for row in baseline["endpoints"]} <= observed


@pytest.fixture
def reader():
    source = DUMP.read_text(encoding="utf-8")
    script = json.loads(
        (PROJECT / "decompile/il2cpp_dump/script.json").read_text(encoding="utf-8")
    )
    reader = ConstructorReader(source, script, BINARY)
    definitions = {definition.name: definition for definition in parse_types(source)}
    try:
        yield reader, definitions
    finally:
        reader.close()


def test_missing_crypto_literal_fails_extraction(reader):
    reader, definitions = reader
    reader.strings = {
        address: value for address, value in reader.strings.items() if value != "aes"
    }
    with pytest.raises(ValueError, match="crypto literal"):
        reader.route(definitions["DrawGachaRequest"])


def test_missing_response_type_fails_extraction(reader):
    reader, definitions = reader
    reader.metadata = {
        address: value
        for address, value in reader.metadata.items()
        if value != "ntd.api.AppEndpointConfigResponse200_var"
    }
    with pytest.raises(ValueError, match="response type references"):
        reader.route(definitions["AppEndpointConfigRequest"])
