# paax-api

Music **metadata and discovery** backend for Paax. Built with FastAPI + ytmusicapi.

> **This service does NOT resolve audio streams.**
> Stream URL resolution lives in **[paax-stream](https://github.com/uzielcezate/paax-stream)** (Invidious-based).

---

## Architecture

```
Flutter (paax-frontend)
    │
    ├── search / metadata / discovery
    │       └── paax-api           ← this repo
    │               └── ytmusicapi → YouTube Music
    │
    └── audio stream URL
            └── paax-stream        ← separate repo
                    └── Invidious  → CDN stream URL
```

| Service | Repo | Responsibility |
|---|---|---|
| **paax-api** | uzielcezate/paax-api | Search, metadata, catalog, library |
| **paax-stream** | uzielcezate/paax-stream | Audio stream URL resolution via Invidious |
| **paax-frontend** | uzielcezate/Paax (frontend/) | Flutter mobile + web app |

---

## What paax-api does

| Endpoint | Description |
|---|---|
| `GET /search` | Full-text music search |
| `GET /home` | Home feed (personalized sections) |
| `GET /home/charts` | Top charts by country |
| `GET /home/discover` | Discovery shelf |
| `GET /home/top` | Top tracks/albums/artists by genre |
| `GET /charts` | Raw ytmusicapi charts |
| `GET /moods` | Mood categories |
| `GET /moods/{params}` | Mood playlists |
| `GET /genre/{slug}` | Genre page (playlists + tracks + artists) |
| `GET /artist/{channelId}` | Artist metadata |
| `GET /artist/{channelId}/albums` | Artist album list |
| `GET /artist/{channelId}/albums/page` | Album list with pagination |
| `GET /album/{browseId}` | Album or playlist detail |
| `GET /song/{videoId}` | Track metadata |
| `GET /song/{videoId}/related` | Related tracks |
| `GET /lyrics/{videoId}` | Lyrics |
| `GET /watch` | Watch queue / radio |
| `GET /library/liked` | Liked songs (authenticated) |
| `GET /library/playlists` | User playlists (authenticated) |
| `POST /rate` | Like/dislike a song (authenticated) |
| `POST /playlists` | Create playlist (authenticated) |
| `DELETE /playlists/{id}` | Delete playlist (authenticated) |
| `POST /playlists/{id}/items` | Add tracks to playlist (authenticated) |
| `DELETE /playlists/{id}/items` | Remove tracks from playlist (authenticated) |
| `GET /health` | Health check |
| `GET /cache/status` | Redis connectivity check |
| `GET /auth/status` | Auth mode check |

## What paax-api does NOT do

- ❌ Resolve audio stream URLs
- ❌ Call Invidious
- ❌ Use yt-dlp
- ❌ Select audio formats or bitrates
- ❌ Proxy or cache stream bytes

---

## Running Locally

### Prerequisites

- Python 3.11+
- (Optional) Redis for caching
- (Optional) `oauth.json` from ytmusicapi for authenticated endpoints

### Setup

```bash
# 1. Clone and enter directory
git clone https://github.com/uzielcezate/paax-api.git
cd paax-api

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate      # macOS/Linux
.venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env: add REDIS_URL and/or YTMUSIC_OAUTH_JSON if needed

# 5. Start the server
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000/docs` for the interactive API explorer.

### Authenticated endpoints (optional)

To enable library, playlists, and rating endpoints, run the ytmusicapi authentication flow once:

```bash
python -c "from ytmusicapi import YTMusic; YTMusic.setup(filepath='oauth.json')"
```

Then leave `oauth.json` in the project root. In production, set `YTMUSIC_OAUTH_JSON` in Railway.

---

## Deploying to Railway

1. Connect this repo to a new Railway service.
2. Railway auto-detects `Procfile` and starts with:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
3. Set the following environment variables in Railway dashboard:

| Variable | Required | Description |
|---|---|---|
| `YTMUSIC_OAUTH_JSON` | Optional | Full JSON content of your ytmusicapi `oauth.json` |
| `REDIS_URL` | Optional | Set automatically when you add a Redis plugin |
| `FRONTEND_ORIGINS` | Optional | Comma-separated production CORS origins |

4. (Optional) Add a Railway Redis plugin — the service works without it but caching improves latency.

---

## API Contract

The frontend expects responses in the shape returned by `ytmusicapi`.
No normalization layer is applied — raw ytmusicapi dicts are returned as-is.
Response shape changes are documented in CHANGELOG.md when introduced.
