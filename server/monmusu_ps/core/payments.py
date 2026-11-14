from collections.abc import Callable
import uuid

from .errors import ApiNotFound


GACHA, SHOP = "gacha", "shop"
UNDELIVERABLE = (ValueError, LookupError, NotImplementedError)
Admission = Callable[[dict, str, int, int], None]


class Payments:
    def __init__(self, masters, admissions: dict[str, Admission]):
        self.masters = masters
        self.admissions = admissions

    def _product(self, sku: str) -> dict:
        return self.masters.lookup("payment_data", "skuId", sku)

    def _admit(self, state: dict, voucher: dict, now: int) -> None:
        product = self._product(voucher["product_sku"])
        self.admissions[product["type"]](
            state, product["productId"], voucher["quantity"], now
        )

    def issue(self, state: dict, sku: str, quantity, now: int) -> str:
        if type(quantity) is not int or quantity < 1:
            raise ValueError("quantity must be a positive integer")
        voucher = {
            "payment_id": uuid.uuid4().hex,
            "product_sku": sku,
            "product_type": self._product(sku)["type"],
            "quantity": quantity,
            "created_at": now,
        }
        self._admit(state, voucher, now)
        state["vouchers"].append(voucher)
        return voucher["payment_id"]

    def pending(self, state: dict, now: int) -> list[dict]:
        kept = []
        for voucher in state["vouchers"]:
            try:
                self._admit(state, voucher, now)
            except UNDELIVERABLE:
                continue
            kept.append(voucher)
        state["vouchers"] = kept
        return kept

    def claim(self, state: dict, payment_id, kind: str) -> tuple[dict, str]:
        for voucher in state["vouchers"]:
            if voucher["payment_id"] == payment_id and voucher["product_type"] == kind:
                return voucher, self._product(voucher["product_sku"])["productId"]
        raise ApiNotFound(str(payment_id))

    @staticmethod
    def settle(state: dict, voucher: dict) -> None:
        state["vouchers"].remove(voucher)
