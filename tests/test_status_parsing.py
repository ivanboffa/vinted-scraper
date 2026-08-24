"""
Lifecycle resolution from an item API response.

Distinguishing *sold* from *deleted* is the point of the dataset, and no single
Vinted field is authoritative across API versions — these cases pin the
precedence rules down.
"""
import pytest

from src.status_checker import _parse_sold_status


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"is_sold": True},                                            "sold"),
        ({"status": "SOLD"},                                           "sold"),
        ({"is_closed": True, "item_closing_action": "sold"},           "sold"),
        ({"is_closed": True, "can_buy": False},                        "sold"),
        ({"is_closed": True, "item_closing_action": "deleted"},        "deleted"),
        ({"is_closed": True},                                          "deleted"),
        ({"is_sold": False, "is_closed": False},                       "active"),
        ({},                                                           "active"),
    ],
)
def test_precedence(body, expected):
    assert _parse_sold_status(body) == expected


def test_reserved_item_is_not_sold():
    """
    can_buy=False on its own means unavailable, not sold — reserved items are
    also unbuyable. Treating it as sold would inflate the sell-through rate.
    """
    assert _parse_sold_status({"can_buy": False, "is_closed": False}) == "active"


def test_accepts_both_wrapped_and_bare_bodies():
    """Some endpoints wrap the payload under "item", some return it bare."""
    assert _parse_sold_status({"item": {"is_sold": True}}) == "sold"
    assert _parse_sold_status({"is_sold": True}) == "sold"
