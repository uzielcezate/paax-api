"""Discography ordering regressions (Phase 3.3 §5).

Within every category releases must sort:
  1. exact release_date descending
  2. release_year descending when the exact date is absent
  3. deterministic title ordering as the final tie-breaker only
Undated/unknown releases sink last.
"""

from mappers import response_mapper as resp
from mappers.release_ordering import sort_releases
from schemas.catalog.artist import ArtistGraph, ReleaseSummary


def _r(id_, title, *, album_type="album", release_date=None, release_year=None):
    return ReleaseSummary(
        id=id_,
        title=title,
        album_type=album_type,
        release_date=release_date,
        release_year=release_year,
    )


def _titles(releases):
    return [r.title for r in releases]


def test_exact_dates_sort_newest_first():
    releases = [
        _r("a", "old", release_date="2019-01-01"),
        _r("b", "new", release_date="2024-06-15"),
        _r("c", "mid", release_date="2021-11-30"),
    ]
    assert _titles(sort_releases(releases)) == ["new", "mid", "old"]


def test_release_year_fallback_when_date_absent():
    releases = [
        _r("a", "y2020", release_year=2020),
        _r("b", "y2023", release_year=2023),
        _r("c", "y2021", release_year=2021),
    ]
    assert _titles(sort_releases(releases)) == ["y2023", "y2021", "y2020"]


def test_iso_date_not_misparsed_as_zero():
    # Regression: the old client/server bug did int(release_date) -> 0 for ISO
    # strings, collapsing all dated releases to the same rank. Full ISO dates
    # must order correctly relative to each other.
    releases = [
        _r("a", "jan", release_date="2023-01-01"),
        _r("b", "dec", release_date="2023-12-31"),
        _r("c", "jun", release_date="2023-06-15"),
    ]
    assert _titles(sort_releases(releases)) == ["dec", "jun", "jan"]


def test_same_year_deterministic_title_tiebreak():
    releases = [
        _r("z", "Zebra", release_year=2022),
        _r("a", "Apple", release_year=2022),
        _r("m", "mango", release_year=2022),
    ]
    # Same recency -> case-insensitive title ascending.
    assert _titles(sort_releases(releases)) == ["Apple", "mango", "Zebra"]


def test_dated_and_year_only_interleave_by_effective_year():
    releases = [
        _r("a", "dated-2021", release_date="2021-05-01"),
        _r("b", "year-2022", release_year=2022),
        _r("c", "year-2020", release_year=2020),
    ]
    assert _titles(sort_releases(releases)) == ["year-2022", "dated-2021", "year-2020"]


def test_undated_unknown_sink_last():
    releases = [
        _r("a", "unknown"),
        _r("b", "dated", release_date="2018-01-01"),
        _r("c", "year", release_year=2015),
    ]
    ordered = _titles(sort_releases(releases))
    assert ordered[-1] == "unknown"
    assert ordered[:2] == ["dated", "year"]


def test_stability_is_idempotent():
    releases = [
        _r("a", "b", release_date="2020-01-01"),
        _r("b", "a", release_date="2020-01-01"),
    ]
    once = _titles(sort_releases(list(releases)))
    twice = _titles(sort_releases(sort_releases(list(releases))))
    assert once == twice == ["a", "b"]  # same date -> title asc, stable


def test_latest_release_regardless_of_type():
    # A single released after the latest album must be the latest release.
    graph = ArtistGraph(
        id="art-x",
        name="X",
        releases=[
            _r("alb", "Album", album_type="album", release_date="2022-01-01"),
            _r("sgl", "Single", album_type="single", release_date="2024-09-09"),
            _r("ep", "EP", album_type="ep", release_date="2023-03-03"),
        ],
    )
    payload = resp.artist_response(graph).model_dump(by_alias=True)
    assert payload["latestRelease"]["title"] == "Single"
    assert payload["latestRelease"]["albumType"] == "single"


def test_discography_categories_each_sorted():
    graph = ArtistGraph(
        id="art-y",
        name="Y",
        releases=[
            _r("al1", "Album Old", album_type="album", release_date="2018-01-01"),
            _r("al2", "Album New", album_type="album", release_date="2024-01-01"),
            _r("s1", "Single Mid", album_type="single", release_year=2021),
            _r("s2", "Single New", album_type="single", release_year=2023),
            _r("e1", "EP One", album_type="ep", release_date="2020-05-05"),
            _r("c1", "Comp", album_type="compilation", release_date="2019-01-01"),
        ],
    )
    payload = resp.artist_response(graph).model_dump(by_alias=True)
    disc = payload["discography"]
    assert [r["title"] for r in disc["albums"]] == ["Album New", "Album Old"]
    assert [r["title"] for r in disc["singles"]] == ["Single New", "Single Mid"]
    assert [r["title"] for r in disc["eps"]] == ["EP One"]
    assert [r["title"] for r in disc["compilations"]] == ["Comp"]
    # Latest across all types = the newest dated album (2024-01-01).
    assert payload["latestRelease"]["title"] == "Album New"
