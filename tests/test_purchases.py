import pytest

from monmusu_ps.app import RUNTIME
from test_server import NativeClient, http, posted


SKU = "com.dmm.dmmgames.monmusutd.stone_2.app_store"
STORE = "PURCHASE_ERROR_IN_TRANSACTION"
TIER = "FORBIDDEN_REQUEST"

__all__ = ["http"]


@pytest.mark.parametrize(
    ("endpoint", "payload", "reason"),
    [
        ("purchase/payment/begin", {"sku_id": SKU, "count": 1}, STORE),
        ("purchase/payment/cancel", {"sku_id": SKU}, STORE),
        ("purchase/payment/confirm/as", {"receipt": "r", "sku_id": SKU}, STORE),
        (
            "purchase/payment/confirm/gp",
            {"receipt": "r", "signature": "s", "sku_id": SKU},
            STORE,
        ),
        ("purchase/tier/confirm", {"tier": "jp_adult"}, TIER),
        ("purchase/information", {"_z0": 0}, TIER),
    ],
)
async def test_store_build_flows_are_refused_on_the_dmm_platform(
    http, endpoint, payload, reason
):
    client = NativeClient(http)
    await client.login()
    runtime = http.app[RUNTIME]
    before = await runtime.store.read("dmm:local")
    response = await posted(http, client, endpoint, payload)
    assert response.status == 422
    assert (await response.json())["data"] == {"rejected_reason": reason}
    assert await runtime.store.read("dmm:local") == before
