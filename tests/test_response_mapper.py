"""Response mapper unit tests: graphs -> normalized camelCase contract."""

import pytest

from mappers import response_mapper as resp
from repositories.catalog.album_repository import AlbumRepository
from repositories.catalog.artist_repository import ArtistRepository
from repositories.catalog.track_repository import TrackRepository


async def test_track_response_shape(gateway):
    g = await TrackRepository(gateway).get_track_by_id("trk-offline")
    payload = resp.track_response(g).model_dump(by_alias=True)

    assert payload["id"] == "trk-offline"
    assert payload["deezerId"] == 123
    assert payload["titleShort"] == "offline"
    assert payload["artistDisplayName"] == "Young Miko, Feid"
    assert payload["artists"][1] == {
        "id": "art-feid", "deezerId": 222, "name": "Feid",
        "role": "featured", "position": 2,
    }
    assert payload["album"]["coverUrl"] == "http://img/att.jpg"
    # Mandatory playback contract.
    pb = payload["playback"]
    assert pb["provider"] == "youtube" and pb["engine"] == "iframe"
    assert pb["videoId"] == "aud123"            # preferred == audio-only
    assert pb["audioVideoId"] == "aud123"
    assert pb["musicVideoId"] == "mv123"
    assert pb["preferredType"] == "audio_only"
    assert pb["matchStatus"] == "matched"
    # metadata_status "full" -> API "fresh"
    assert payload["metadataStatus"] == "fresh"


async def test_album_response_totals_and_tracks(gateway):
    g = await AlbumRepository(gateway).get_album_with_tracks("alb-att")
    payload = resp.album_response(g).model_dump(by_alias=True)
    assert payload["totalTracks"] == 2
    assert payload["totalDurationSeconds"] == 360
    assert len(payload["tracks"]) == 2
    # Not "Unknown Artist" / 0 songs — real stored metadata.
    assert payload["artists"][0]["name"] == "Young Miko"


async def test_artist_response_discography_and_latest(gateway):
    g = await ArtistRepository(gateway).get_artist_with_releases("art-miko")
    payload = resp.artist_response(g).model_dump(by_alias=True)

    assert payload["name"] == "Young Miko"
    assert payload["deezerFansCount"] == 500000
    # latest dated release is att. (2024-04-05)
    assert payload["latestRelease"]["releaseDate"] == "2024-04-05"
    # album vs single bucketing
    album_titles = [r["title"] for r in payload["discography"]["albums"]]
    single_titles = [r["title"] for r in payload["discography"]["singles"]]
    assert "att." in album_titles and "trap kitty" in album_titles
    assert "classy 101" in single_titles
