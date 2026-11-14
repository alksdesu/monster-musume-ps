from ..core.context import Context
from ..core.errors import ApiRejected
from ..core.sync import Result


# Store builds own these flows; DMM purchases arrive through the platform SDK.
def store_transaction(ctx: Context) -> Result:
    raise ApiRejected("PURCHASE_ERROR_IN_TRANSACTION")


def spending_tier(ctx: Context) -> Result:
    raise ApiRejected("FORBIDDEN_REQUEST")


ROUTES = {
    "purchase/payment/begin": store_transaction,
    "purchase/payment/cancel": store_transaction,
    "purchase/payment/confirm/as": store_transaction,
    "purchase/payment/confirm/gp": store_transaction,
    "purchase/tier/confirm": spending_tier,
    "purchase/information": spending_tier,
}
