"""
PlaybackMatchingService — resolve YouTube playback IDs from canonical catalog
metadata and PERSIST them to tracks.youtube_*.

Rules honored here (mandatory playback-selection):
  * preferred_youtube_video_id defaults to the audio slot; MV only when no audio.
  * a valid audio preferred is NEVER replaced merely because an MV was found.
  * both audio and MV mappings + their match_types are stored.
  * canonical Deezer/Supabase metadata is never touched (writes are whitelisted).
Artist/album reads never block on this — missing matches are scheduled with
bounded concurrency; on-demand resolution persists and returns the video ID.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import settings
from schemas.catalog.common import YoutubeMatchStatus
from services.deezer.errors import DeezerError
from services.youtube.track_matcher import MatchResult, match_track

logger = logging.getLogger("paax.match")

_MAX_FAILURES = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlaybackMatchingService:
    def __init__(self, *, track_repo, jobs, catalog_service=None, matcher=match_track,
                 max_concurrency: Optional[int] = None):
        self.tracks = track_repo
        self.jobs = jobs
        self.catalog = catalog_service
        self.matcher = matcher
        self._sem = asyncio.Semaphore(max_concurrency or settings.limits.max_youtube_match_concurrency)

    # ── on-demand resolution ────────────────────────────────────────────────

    async def resolve_and_persist(self, track_id: str, *, force: bool = False) -> Dict[str, Any]:
        graph = await self.tracks.get_track_by_id(track_id)
        if graph is None:
            return _failed_playback()

        pb = graph.playback
        if (not force and pb.preferred_youtube_video_id
                and pb.youtube_match_status == YoutubeMatchStatus.MATCHED
                and (pb.youtube_failure_count or 0) == 0):
            return _playback_from_state(pb)

        async with self._sem:  # bound concurrent YouTube matching
            result = await self.matcher(self._build_meta(graph))

        fields = self._merge_fields(pb, result)
        await self.tracks.update_youtube_match(track_id, fields)
        await self._invalidate(graph)
        logger.info("[match] track=%s -> preferred=%s status=%s",
                    track_id, fields.get("preferred_youtube_video_id"),
                    fields.get("youtube_match_status"))
        return _playback_from_fields(fields)

    # ── playback-failure revalidation ───────────────────────────────────────

    async def report_failure(self, track_id: str) -> Dict[str, Any]:
        row = await self.tracks.get_row_by_id(track_id)
        if row is None:
            return _failed_playback()
        count = (row.get("youtube_failure_count") or 0) + 1
        give_up = count >= _MAX_FAILURES
        status = YoutubeMatchStatus.UNAVAILABLE if give_up else YoutubeMatchStatus.STALE
        await self.tracks.update_youtube_match(track_id, {
            "youtube_failure_count": count,
            "youtube_match_status": status,
            "youtube_last_verified_at": _now_iso(),
        })
        logger.info("[match] playback failure track=%s count=%d -> %s", track_id, count, status)
        if give_up:
            # Stop retrying; the catalog track is always kept.
            return _failed_playback()
        # Try to find a replacement audio/MV.
        return await self.resolve_and_persist(track_id, force=True)

    # ── bounded background scheduling (album/artist pages) ──────────────────

    def schedule_missing_matches(self, graphs: List[Any]) -> int:
        scheduled = 0
        pending_states = {YoutubeMatchStatus.PENDING, YoutubeMatchStatus.STALE,
                          YoutubeMatchStatus.FAILED}
        for g in graphs:
            pb = getattr(g, "playback", None)
            if pb is None:
                continue
            if not pb.preferred_youtube_video_id and pb.youtube_match_status in pending_states:
                self.jobs.schedule(
                    lambda gid=g.id: self.resolve_and_persist(gid),
                    label=f"match:{g.id}",
                )
                scheduled += 1
        return scheduled

    def schedule_ids(self, track_ids) -> int:
        """Schedule background matching for specific track ids (non-blocking)."""
        scheduled = 0
        for tid in track_ids:
            if tid:
                self.jobs.schedule(lambda t=tid: self.resolve_and_persist(t), label=f"match:{tid}")
                scheduled += 1
        return scheduled

    # ── internals ───────────────────────────────────────────────────────────

    def _build_meta(self, graph) -> Dict[str, Any]:
        primary = next((a.name for a in graph.artists if a.role == "primary"),
                       graph.artists[0].name if graph.artists else "")
        return {
            "title": graph.title,
            "primary_artist": primary,
            "display_artists": graph.artist_display_name,
            "duration": graph.duration_seconds or 0,
            "explicit": graph.explicit,
        }

    def _merge_fields(self, pb, result: MatchResult) -> Dict[str, Any]:
        audio = result.audio
        mv = result.music_video
        new_audio_id = audio.video_id if audio else pb.youtube_audio_video_id
        new_audio_type = audio.match_type if audio else pb.youtube_audio_match_type
        new_mv_id = mv.video_id if mv else pb.youtube_music_video_id
        new_mv_type = mv.match_type if mv else pb.youtube_music_video_match_type

        # Preferred: audio-first; never replace a valid audio preferred with MV.
        if new_audio_id:
            preferred, conf = new_audio_id, (audio.confidence if audio else pb.youtube_match_confidence)
        elif new_mv_id:
            preferred, conf = new_mv_id, (mv.confidence if mv else None)
        else:
            preferred, conf = None, None

        if preferred is None:
            status = YoutubeMatchStatus.FAILED
        elif (conf or 0) >= 0.5:
            status = YoutubeMatchStatus.MATCHED
        else:
            status = YoutubeMatchStatus.NEEDS_REVIEW

        reason = (audio.reason if audio else None) or (mv.reason if mv else None) or "no candidate"

        fields: Dict[str, Any] = {
            "youtube_audio_video_id": new_audio_id,
            "youtube_audio_match_type": new_audio_type,
            "youtube_music_video_id": new_mv_id,
            "youtube_music_video_match_type": new_mv_type,
            "preferred_youtube_video_id": preferred,
            "youtube_match_status": status,
            "youtube_match_confidence": conf,
            "youtube_match_reason": reason,
            "youtube_match_updated_at": _now_iso(),
            "youtube_last_verified_at": _now_iso(),
        }
        if preferred is not None:
            fields["youtube_failure_count"] = 0
        return fields

    async def _invalidate(self, graph) -> None:
        if self.catalog is None:
            return
        try:
            await self.catalog.invalidate_track(deezer_id=graph.deezer_id, internal_id=graph.id)
            if graph.album is not None:
                await self.catalog.invalidate_album(deezer_id=graph.album.deezer_id,
                                                    internal_id=graph.album.id)
        except Exception as exc:  # noqa: BLE001 — invalidation is best-effort
            logger.warning("[match] cache invalidation failed: %s", exc)


# ── playback dict builders (mandatory response contract) ────────────────────

def _preferred_type_for(fields: Dict[str, Any]) -> Optional[str]:
    pref = fields.get("preferred_youtube_video_id")
    if not pref:
        return None
    if pref == fields.get("youtube_audio_video_id"):
        return fields.get("youtube_audio_match_type")
    if pref == fields.get("youtube_music_video_id"):
        return fields.get("youtube_music_video_match_type")
    return None


def _playback_from_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "youtube", "engine": "iframe",
        "videoId": fields.get("preferred_youtube_video_id"),
        "audioVideoId": fields.get("youtube_audio_video_id"),
        "musicVideoId": fields.get("youtube_music_video_id"),
        "preferredType": _preferred_type_for(fields),
        "matchStatus": fields.get("youtube_match_status"),
        "matchConfidence": fields.get("youtube_match_confidence"),
    }


def _playback_from_state(pb) -> Dict[str, Any]:
    return {
        "provider": "youtube", "engine": "iframe",
        "videoId": pb.preferred_youtube_video_id,
        "audioVideoId": pb.youtube_audio_video_id,
        "musicVideoId": pb.youtube_music_video_id,
        "preferredType": pb.preferred_type,
        "matchStatus": pb.youtube_match_status,
        "matchConfidence": pb.youtube_match_confidence,
    }


def _failed_playback() -> Dict[str, Any]:
    return {
        "provider": "youtube", "engine": "iframe", "videoId": None,
        "audioVideoId": None, "musicVideoId": None, "preferredType": None,
        "matchStatus": YoutubeMatchStatus.FAILED, "matchConfidence": None,
    }
