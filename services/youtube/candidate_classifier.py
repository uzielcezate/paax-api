"""
Classify a YouTube candidate against canonical Deezer metadata.

Types (schemas.catalog.common.MatchType):
  audio_only            Topic / official-audio / auto album-art / visualizer
  lyric_video           official lyric video (audio-acceptable fallback)
  official_music_video  official MV / VEVO
  live / remix / cover  version mismatches (rejected unless in the Deezer title)
  other                 plain upload (weak, last-resort audio candidate)

A candidate is *rejected* when it carries a version marker (live, cover, sped up,
slowed, nightcore, karaoke, reaction, instrumental, remix, …) that is NOT present
in the Deezer title — i.e. it is a different rendition than the canonical track.
"""

from __future__ import annotations

from typing import Tuple

from schemas.catalog.common import MatchType

# Markers that make a candidate a different rendition; rejected when absent from
# the Deezer title.
_REJECT_MARKERS = [
    "reaction", "karaoke", "nightcore", "8d audio", "8d", "bass boosted",
    "cover", "tutorial", "drum cover", "guitar cover", "piano cover",
    "mashup", "parody", "sped up", "speed up", "slowed", "reverb",
    "instrumental",
]
_VERSION_MARKERS = ["live", "remix", "acoustic"]


def _present(needle: str, haystack: str) -> bool:
    return needle in haystack


def classify(cand_title: str, cand_channel: str, dz_title: str) -> Tuple[str, bool, str]:
    """Return (match_type, rejected, reason)."""
    t = (cand_title or "").lower()
    ch = (cand_channel or "").lower()
    dz = (dz_title or "").lower()

    # Hard rejects — a different rendition not asked for by the canonical title.
    for kw in _REJECT_MARKERS:
        if _present(kw, t) and not _present(kw, dz):
            mtype = MatchType.COVER if "cover" in kw else (
                MatchType.REMIX if kw == "remix" else MatchType.OTHER)
            return mtype, True, f"reject:{kw}"

    if _present("live", t) and not _present("live", dz):
        return MatchType.LIVE, True, "reject:live"
    if _present("remix", t) and not _present("remix", dz):
        return MatchType.REMIX, True, "reject:remix"

    # Eligible classifications, strongest audio signal first.
    if ch.endswith("- topic"):
        return MatchType.AUDIO_ONLY, False, "topic"
    if "official audio" in t or "audio oficial" in t:
        return MatchType.AUDIO_ONLY, False, "official-audio"
    if "visualizer" in t or "visualiser" in t:
        return MatchType.AUDIO_ONLY, False, "visualizer"
    if "lyric" in t:
        return MatchType.LYRIC_VIDEO, False, "lyric-video"
    if ("official music video" in t or "official video" in t
            or "video oficial" in t or "vevo" in ch):
        return MatchType.OFFICIAL_MUSIC_VIDEO, False, "official-music-video"
    return MatchType.OTHER, False, "plain"


#: Types acceptable in the audio slot (best-first preference is applied by score).
AUDIO_TYPES = {MatchType.AUDIO_ONLY, MatchType.LYRIC_VIDEO, MatchType.OTHER}
