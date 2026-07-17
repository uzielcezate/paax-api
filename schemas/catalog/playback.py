"""
Playback (YouTube match) schemas.

Deezer/Supabase metadata is always canonical; YouTube supplies *only* the
playback video IDs. ``preferred_youtube_video_id`` defaults to the audio-only
match and is what the Flutter iframe receives as ``playback.videoId``.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .common import CamelModel, MatchType, YoutubeMatchStatus


class PlaybackState(BaseModel):
    """Internal representation of a track's stored YouTube mapping."""

    youtube_audio_video_id: Optional[str] = None
    youtube_music_video_id: Optional[str] = None
    preferred_youtube_video_id: Optional[str] = None
    youtube_audio_match_type: Optional[str] = None
    youtube_music_video_match_type: Optional[str] = None
    youtube_match_status: str = YoutubeMatchStatus.PENDING
    youtube_match_confidence: Optional[float] = None
    youtube_match_reason: Optional[str] = None
    youtube_failure_count: int = 0

    @property
    def preferred_type(self) -> Optional[str]:
        """Classification of the preferred (default) playback source."""
        pref = self.preferred_youtube_video_id
        if not pref:
            return None
        if pref == self.youtube_audio_video_id:
            return self.youtube_audio_match_type or MatchType.AUDIO_ONLY
        if pref == self.youtube_music_video_id:
            return self.youtube_music_video_match_type or MatchType.OFFICIAL_MUSIC_VIDEO
        return None


class PlaybackResponse(CamelModel):
    """
    Stable playback block returned to Flutter. ``videoId`` is the preferred
    (normally audio-only) YouTube ID the existing iframe consumes unchanged.
    """

    provider: str = "youtube"
    engine: str = "iframe"
    video_id: Optional[str] = None          # == preferred_youtube_video_id
    audio_video_id: Optional[str] = None
    music_video_id: Optional[str] = None
    preferred_type: Optional[str] = None
    match_status: str = YoutubeMatchStatus.PENDING
    match_confidence: Optional[float] = None
