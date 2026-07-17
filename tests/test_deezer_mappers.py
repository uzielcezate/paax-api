"""Deezer -> payload mapper unit tests."""

from mappers.deezer_album_mapper import _album_artists, album_graph_payload
from mappers.deezer_common import album_type, build_track_artists, slugify
from mappers.deezer_track_mapper import parse_release, track_graph_payload


def test_slugify_matches_db_constraint():
    assert slugify("Hip Hop / Rap") == "hip-hop-rap"
    assert slugify("R&B") == "r-b"
    assert slugify("Pop") == "pop"


def test_album_type_mapping():
    assert album_type("compile", 20) == "compilation"
    assert album_type(None, 1) == "single"
    assert album_type(None, 5) == "ep"
    assert album_type("weird", 12) == "album"


def test_parse_release_rejects_zero_date():
    assert parse_release("2024-04-05") == ("2024-04-05", 2024)
    assert parse_release("0000-00-00") == (None, None)
    assert parse_release("2024-00-00") == (None, 2024)
    assert parse_release(None) == (None, None)


def test_build_track_artists_preserves_features():
    dz_track = {
        "artist": {"id": 111, "name": "Young Miko"},
        "contributors": [
            {"id": 111, "name": "Young Miko", "role": "Main"},
            {"id": 222, "name": "Feid", "role": "Featured"},
        ],
    }
    artists = build_track_artists(dz_track)
    assert [(a["name"], a["role"], a["position"]) for a in artists] == [
        ("Young Miko", "primary", 1),
        ("Feid", "featured", 2),
    ]


def test_track_graph_payload_partial_vs_full():
    dz = {
        "id": 123, "title": "offline", "title_short": "offline", "isrc": "US1",
        "duration": 180, "track_position": 6, "disk_number": 1, "explicit_lyrics": True,
        "bpm": 0, "artist": {"id": 111, "name": "Young Miko"},
        "album": {"id": 333, "title": "att.", "cover_xl": "http://c.jpg", "release_date": "2024-04-05"},
        "contributors": [{"id": 111, "name": "Young Miko", "role": "Main"},
                          {"id": 222, "name": "Feid", "role": "Featured"}],
    }
    p = track_graph_payload(dz, metadata_status="full", complete=True)
    assert p["complete"] is True
    assert p["track"]["metadata_status"] == "full"
    assert p["track"]["bpm"] is None            # 0 bpm -> None (DB CHECK bpm > 0)
    assert p["track"]["isrc"] == "US1"
    assert len(p["track"]["artists"]) == 2
    assert p["album"]["metadata_status"] == "partial"
    assert p["album"]["release_date"] == "2024-04-05"


# ── Phase 2.6: discography attribution ───────────────────────────────────────

_CTX = {"deezer_id": 27, "name": "Daft Punk"}


def test_album_artists_uses_parent_context_when_deezer_omits_artist():
    # /artist/{id}/albums entry: no 'artist', no 'contributors' (the bug trigger).
    aa = _album_artists({"id": 302, "title": "Discovery"}, _CTX)
    assert aa == [{"deezer_id": 27, "name": "Daft Punk", "role": "primary", "position": 1}]


def test_album_artists_preserves_explicit_and_does_not_duplicate_parent():
    dz_album = {"id": 1, "artist": {"id": 27, "name": "Daft Punk"},
                "contributors": [{"id": 27, "name": "Daft Punk", "role": "Main"}]}
    aa = _album_artists(dz_album, _CTX)
    assert [a["name"] for a in aa] == ["Daft Punk"]     # parent already present → no dupe
    assert len(aa) == 1


def test_album_artists_appends_parent_when_absent_from_explicit():
    dz_album = {"id": 2, "artist": {"id": 999, "name": "Various Artists"}}
    aa = _album_artists(dz_album, _CTX)
    assert [(a["name"], a["position"]) for a in aa] == [
        ("Various Artists", 1), ("Daft Punk", 2)]        # explicit preserved, parent appended


def test_album_artists_no_placeholder_without_context():
    assert _album_artists({"id": 3, "title": "Mystery"}, None) == []   # never "Unknown Artist"


def test_album_artists_preserves_valid_collaborators():
    dz_album = {"id": 4, "artist": {"id": 27, "name": "Daft Punk"},
                "contributors": [{"id": 27, "name": "Daft Punk", "role": "Main"},
                                 {"id": 55, "name": "Pharrell Williams", "role": "Featured"}]}
    aa = _album_artists(dz_album, _CTX)
    assert [a["name"] for a in aa] == ["Daft Punk", "Pharrell Williams"]
    assert aa[1]["role"] == "featured"


def test_album_graph_payload_partial_and_no_placeholder_without_artist():
    p = album_graph_payload({"id": 3, "title": "Mystery", "nb_tracks": 1}, None,
                            complete=False, metadata_status="full")
    assert p["album_artists"] == []
    assert p["album"]["metadata_status"] == "partial"   # downgraded (no attribution)


def test_album_graph_payload_attributes_to_parent_context():
    p = album_graph_payload({"id": 302, "title": "Discovery", "record_type": "album",
                             "nb_tracks": 14}, None, complete=False,
                            metadata_status="partial", artist_context=_CTX)
    assert [a["deezer_id"] for a in p["album_artists"]] == [27]
    assert not any(a["name"] == "Unknown Artist" for a in p["album_artists"])


def test_album_graph_payload_uses_full_track_objs_for_credits():
    dz_album = {
        "id": 333, "title": "att.", "record_type": "album", "release_date": "2024-04-05",
        "label": "Sony", "nb_tracks": 2, "duration": 360, "explicit_lyrics": True,
        "cover_xl": "http://cover.jpg", "artist": {"id": 111, "name": "Young Miko"},
        "contributors": [{"id": 111, "name": "Young Miko", "role": "Main"}],
        "genres": {"data": [{"id": 197, "name": "Latin"}]},
        "tracks": {"data": [
            {"id": 124, "title": "solo", "track_position": 1, "disk_number": 1,
             "duration": 180, "artist": {"id": 111, "name": "Young Miko"}},
            {"id": 123, "title": "offline", "track_position": 6, "disk_number": 1,
             "duration": 180, "artist": {"id": 111, "name": "Young Miko"}},
        ]},
    }
    # Full /track/{id} objects add the featured collaborator on track 123.
    track_objs = {
        123: {"id": 123, "title": "offline", "track_position": 6, "disk_number": 1,
              "duration": 180, "isrc": "US1", "artist": {"id": 111, "name": "Young Miko"},
              "contributors": [{"id": 111, "name": "Young Miko", "role": "Main"},
                               {"id": 222, "name": "Feid", "role": "Featured"}]},
    }
    p = album_graph_payload(dz_album, track_objs, complete=True)
    assert p["complete"] is True
    assert p["album"]["album_type"] == "album"
    assert p["album"]["release_year"] == 2024
    assert [a["name"] for a in p["album_artists"]] == ["Young Miko"]
    assert [g["slug"] for g in p["album_genres"]] == ["latin"]

    tracks_by_id = {t["deezer_id"]: t for t in p["tracks"]}
    # Track 123 keeps Feid; track 124 stays solo.
    assert [a["name"] for a in tracks_by_id[123]["artists"]] == ["Young Miko", "Feid"]
    assert [a["name"] for a in tracks_by_id[124]["artists"]] == ["Young Miko"]
    assert tracks_by_id[123]["image_original_url"] == "http://cover.jpg"
