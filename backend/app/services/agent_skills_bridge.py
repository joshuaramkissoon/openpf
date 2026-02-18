from __future__ import annotations

from typing import Any

from app.models.entities import TradeIntent


# Bridge payload format aligned with the Trading212 Labs agent-skills action style.
def intent_to_skill_action(intent: TradeIntent) -> dict[str, Any]:
    signed_quantity = intent.quantity if intent.side == "buy" else -abs(intent.quantity)

    if intent.order_type == "limit" and intent.limit_price is not None:
        return {
            "action": "place_limit_order",
            "params": {
                "ticker": intent.instrument_code,
                "quantity": signed_quantity,
                "limitPrice": intent.limit_price,
            },
        }

    if intent.order_type == "stop" and intent.stop_price is not None:
        return {
            "action": "place_stop_order",
            "params": {
                "ticker": intent.instrument_code,
                "quantity": signed_quantity,
                "stopPrice": intent.stop_price,
            },
        }

    if intent.order_type == "stop_limit" and intent.stop_price is not None and intent.limit_price is not None:
        return {
            "action": "place_stop_limit_order",
            "params": {
                "ticker": intent.instrument_code,
                "quantity": signed_quantity,
                "stopPrice": intent.stop_price,
                "limitPrice": intent.limit_price,
            },
        }

    return {
        "action": "place_market_order",
        "params": {
            "ticker": intent.instrument_code,
            "quantity": signed_quantity,
            "extendedHours": False,
        },
    }
