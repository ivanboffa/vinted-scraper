"""
Sold detection from an item's web page.

Vinted migrated from the Next.js Pages Router to the App Router mid-project:
item state moved out of a parseable __NEXT_DATA__ tag and into React Server
Component chunks, where the JSON arrives as an *escaped* JS string. These cases
cover both eras plus the text fallback, so a future migration degrades rather
than silently misclassifying.
"""
from src.api_client import _parse_web_data

RSC_MARKER = 'self.__next_f.push([1,"payload"])'


def _sold(html):
    """First element of the 5-tuple: True sold, False not sold, None unknown."""
    return _parse_web_data(html)[0]


# ── App Router: escaped JSON inside RSC chunks ───────────────────────────

def test_rsc_closed_and_sold():
    html = RSC_MARKER + r'\"is_closed\":true,\"item_closing_action\":\"sold\"'
    assert _sold(html) is True


def test_rsc_closed_without_action_counts_as_sold():
    assert _sold(RSC_MARKER + r'\"is_closed\":true') is True


def test_rsc_closed_for_another_reason_is_not_sold():
    html = RSC_MARKER + r'\"is_closed\":true,\"item_closing_action\":\"deleted\"'
    assert _sold(html) is False


def test_rsc_open_listing_is_not_sold():
    assert _sold(RSC_MARKER + r'\"is_closed\":false') is False


# ── Pages Router: legacy __NEXT_DATA__ ───────────────────────────────────

def test_legacy_next_data_sold_and_engagement():
    html = (
        '<script id="__NEXT_DATA__">'
        '{"props":{"pageProps":{"item":'
        '{"is_sold":true,"view_count":42,"favourite_count":7}}}}'
        '</script>'
    )
    sold, views, favourites, _, _ = _parse_web_data(html)
    assert sold is True
    assert (views, favourites) == (42, 7)


def test_legacy_next_data_active():
    html = (
        '<script id="__NEXT_DATA__">'
        '{"props":{"pageProps":{"item":{"is_sold":false}}}}'
        '</script>'
    )
    assert _sold(html) is False


# ── Fallbacks ────────────────────────────────────────────────────────────

def test_visible_text_fallback():
    assert _sold("<p>Questo articolo è stato venduto</p>") is True


def test_unrecognised_page_is_undecided():
    """None means "cannot determine" — it must not collapse into False."""
    assert _sold("<html><body>cookie banner</body></html>") is None
