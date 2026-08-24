"""Category filtering — the scope switch that keeps the corpus to donna + uomo."""
from src.categories import filter_categories

CATS = [
    (1, "donna/vestiti"),
    (2, "uomo/jeans"),
    (3, "bambini/scarpe"),
    (4, "elettronica/telefoni"),
]


def test_excluded_prefixes_are_removed():
    assert filter_categories(CATS, ["bambini", "elettronica"]) == [
        (1, "donna/vestiti"),
        (2, "uomo/jeans"),
    ]


def test_matching_is_case_insensitive():
    assert filter_categories(CATS, ["BAMBINI"]) == [
        (1, "donna/vestiti"),
        (2, "uomo/jeans"),
        (4, "elettronica/telefoni"),
    ]


def test_empty_exclusion_list_keeps_everything():
    assert filter_categories(CATS, []) == CATS


def test_prefix_match_is_anchored_at_the_start():
    """"scarpe" appears mid-path in bambini/scarpe and must not match."""
    assert filter_categories(CATS, ["scarpe"]) == CATS
