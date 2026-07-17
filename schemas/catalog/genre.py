"""Genre schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .common import CamelModel


class GenreRow(BaseModel):
    """Mirror of a public.genres row (subset used by the catalog)."""

    id: str
    deezer_id: Optional[int] = None
    name: str
    slug: Optional[str] = None
    image_original_url: Optional[str] = None
    image_cached_url: Optional[str] = None
    relevance_score: Optional[float] = None  # populated from a junction row


class GenreResponse(CamelModel):
    id: str
    deezer_id: Optional[int] = None
    name: str
    slug: Optional[str] = None
