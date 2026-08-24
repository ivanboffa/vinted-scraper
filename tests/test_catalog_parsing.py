"""
Catalog item normalisation.

Vinted returns the same field in several shapes depending on endpoint and API
version. _parse_item has to flatten all of them into one row without ever
raising — a single malformed item must not abort a category page.
"""
from src.async_scraper import _parse_item


def test_item_without_id_is_rejected():
    assert _parse_item({"title": "no id"}, "donna/vestiti") is None


def test_unexpected_field_shape_does_not_abort_the_page():
    """A malformed field degrades to a default; it must never raise upward."""
    row = _parse_item({"id": 1, "photos": object()}, "donna/vestiti")
    assert row is not None and row["photo_count"] == 0


def test_price_as_nested_object():
    row = _parse_item(
        {"id": 1, "price": {"amount": "12.50", "currency_code": "EUR"}},
        "donna/vestiti",
    )
    assert row["price"] == 12.50
    assert row["currency"] == "EUR"


def test_price_with_comma_decimal_separator():
    row = _parse_item({"id": 1, "price": "12,50"}, "donna/vestiti")
    assert row["price"] == 12.50


def test_unparseable_price_becomes_none_not_zero():
    """A missing price must stay distinguishable from a genuine 0.00 listing."""
    row = _parse_item({"id": 1, "price": "gratis"}, "donna/vestiti")
    assert row["price"] is None


def test_photos_list_yields_url_and_count():
    row = _parse_item(
        {"id": 1, "photos": [{"full_size_url": "https://img/1"}, {"url": "https://img/2"}]},
        "donna/vestiti",
    )
    assert row["image_url"] == "https://img/1"
    assert row["photo_count"] == 2


def test_absent_photos_count_as_zero():
    row = _parse_item({"id": 1}, "donna/vestiti")
    assert row["photo_count"] == 0
    assert row["image_url"] is None


def test_condition_falls_back_to_status_id_lookup():
    row = _parse_item({"id": 1, "status_id": 1}, "donna/vestiti")
    assert row["condition"] == "nuovo con etichetta"


def test_seller_and_country_are_normalised():
    row = _parse_item(
        {"id": 1, "user": {"id": 99, "items_count": 12, "country": {"iso_code": "it"}}},
        "donna/vestiti",
    )
    assert row["seller_id"] == "99"          # always a string, ids arrive as int or str
    assert row["seller_item_count"] == 12
    assert row["country_iso_code"] == "IT"   # upper-cased, two letters


def test_zero_engagement_is_preserved_not_dropped():
    """0 views is data; it must not be coerced to None by a falsy check."""
    row = _parse_item({"id": 1, "view_count": 0, "favourite_count": 0}, "donna/vestiti")
    assert row["views_count"] == 0
    assert row["favourite_count"] == 0


def test_category_path_is_carried_through():
    assert _parse_item({"id": 1}, "uomo/jeans")["category"] == "uomo/jeans"
