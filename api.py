"""FastAPI entrypoint exposing the Smash Character Competency analytics."""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from smashcc.analysis import (
    find_tournaments,
    generate_player_metrics,
    generate_player_metrics_for_tournaments,
)
from smashcc.datastore import SQLiteStore
from smashcc.smash_data import fetch_tournament_events, is_singles_event
from smashcc.startgg_client import StartGGClient

app = FastAPI(
    title="Smash Character Competency API",
    description="Lightweight API exposing player metrics derived from start.gg data.",
    version="0.1.0",
)

DEFAULT_STORE_PATH = Path(".cache") / "startgg" / "smash.db"


class RateLimiter:
    """Simple in-memory per-IP limiter using a sliding window."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = max(0, int(limit))
        self.window = max(1, int(window_seconds))
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def describe(self) -> str:
        if not self.enabled:
            return "unlimited"
        unit = "second" if self.window == 1 else "seconds"
        return f"{self.limit} requests per {self.window} {unit}"

    async def check(self, identity: str) -> Optional[float]:
        """Record a hit and return retry-after seconds when over the limit."""
        if not self.enabled:
            return None
        now = time.monotonic()
        window_start = now - self.window
        async with self._lock:
            dq = self._hits.get(identity)
            if dq is None:
                dq = deque()
                self._hits[identity] = dq
            while dq and dq[0] <= window_start:
                dq.popleft()
            if len(dq) >= self.limit:
                retry_after = max(0.0, self.window - (now - dq[0]))
                return retry_after
            dq.append(now)
            return None


RATE_LIMIT_REQUESTS = int(os.getenv("SMASHCC_RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.getenv("SMASHCC_RATE_LIMIT_WINDOW", "60"))
rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)


def _get_store_path() -> Path:
    """Resolve the SQLite path, allowing overrides via SMASHCC_DB_PATH."""
    override = os.environ.get("SMASHCC_DB_PATH")
    return Path(override).expanduser() if override else DEFAULT_STORE_PATH


def _load_precomputed_metrics(
    *,
    state: str,
    months_back: int,
    videogame_id: int,
    target_character: str,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch persisted metric rows for the requested parameters."""
    store = SQLiteStore(_get_store_path())
    try:
        return store.load_player_metrics(
            state=state,
            videogame_id=videogame_id,
            months_back=months_back,
            target_character=target_character,
            limit=limit,
        )
    finally:
        store.close()


def _load_series_metrics(
    *,
    state: str,
    months_back: int,
    videogame_id: int,
    window_offset: int,
    window_size: Optional[int],
    series_key: str,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch persisted series metric rows for the requested parameters."""
    store = SQLiteStore(_get_store_path())
    try:
        return store.load_series_metrics(
            state=state,
            videogame_id=videogame_id,
            months_back=months_back,
            window_offset=window_offset,
            window_size=window_size,
            series_key=series_key,
            limit=limit,
        )
    finally:
        store.close()


def _parse_start_after_timestamp(start_after: Optional[str]) -> Optional[int]:
    """Convert the start_after string into a UTC timestamp suitable for filtering."""
    if not start_after:
        return None
    try:
        cutoff = datetime.fromisoformat(start_after).replace(tzinfo=timezone.utc)
    except ValueError as exc:  # pragma: no cover - request validation
        raise HTTPException(
            status_code=400,
            detail=f"Invalid start_after date '{start_after}'. Expected YYYY-MM-DD.",
        ) from exc
    return int(cutoff.timestamp())


def _normalize_terms(values: Optional[List[str]]) -> List[str]:
    """Return lowercase, trimmed substring filters."""
    return [v.strip().lower() for v in values or [] if v and v.strip()]


def _extract_tournament_slug(value: Optional[str]) -> Optional[str]:
    """Pull the tournament slug out of either a slug or a full start.gg URL."""
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    anchor = "tournament/"
    if "start.gg" in lower:
        start_idx = lower.find(anchor)
        slug_part = lower[start_idx:] if start_idx != -1 else lower
    else:
        slug_part = lower
    slug_part = slug_part.lstrip("/")
    slug_part = slug_part.split("?", 1)[0].split("#", 1)[0]
    segments = slug_part.split("/")
    if len(segments) >= 2:
        slug_part = "/".join(segments[:2])
    if not slug_part.startswith(anchor):
        return None
    return slug_part


def _apply_common_filters(
    df: pd.DataFrame,
    *,
    filter_state: Optional[List[str]] = None,
    min_entrants: Optional[int] = None,
    max_entrants: Optional[int] = None,
    min_max_event_entrants: Optional[int] = None,
    min_large_event_share: Optional[float] = None,
    start_after_ts: Optional[int] = None,
) -> pd.DataFrame:
    """Apply shared filtering semantics used by multiple endpoints."""
    filtered = df
    if filter_state:
        allowed = {code.upper() for code in filter_state if code}
        if allowed and "home_state" in filtered.columns:
            state_series = filtered["home_state"].fillna("").str.upper()
            filtered = filtered[state_series.isin(allowed)]

    if min_entrants is not None and "avg_event_entrants" in filtered.columns:
        filtered = filtered[filtered["avg_event_entrants"].fillna(0) >= min_entrants]

    if max_entrants is not None and "avg_event_entrants" in filtered.columns:
        filtered = filtered[filtered["avg_event_entrants"].fillna(0) <= max_entrants]

    if (
        min_max_event_entrants is not None
        and "max_event_entrants" in filtered.columns
    ):
        filtered = filtered[
            filtered["max_event_entrants"].fillna(0) >= min_max_event_entrants
        ]

    if (
        min_large_event_share is not None
        and "large_event_share" in filtered.columns
    ):
        filtered = filtered[
            filtered["large_event_share"].fillna(0) >= min_large_event_share
        ]

    if start_after_ts is not None and "latest_event_start" in filtered.columns:
        filtered = filtered[filtered["latest_event_start"].fillna(0) >= start_after_ts]

    return filtered


def _find_series(
    *,
    state: str,
    videogame_id: int,
    months_back: int,
    window_offset: int,
    window_size: Optional[int],
    series_key: Optional[str],
    tournament_contains: Optional[List[str]],
    tournament_slug_contains: Optional[List[str]],
    allow_multi: bool = False,
) -> List[Dict[str, Any]]:
    """Resolve one or more series keys using provided hints."""
    if series_key:
        return [
            {
                "series_key": series_key,
                "series_name_term": None,
                "series_slug_term": None,
            }
        ]
    name_term = (tournament_contains or [None])[0]
    slug_term = (tournament_slug_contains or [None])[0]
    if not name_term and not slug_term:
        raise HTTPException(
            status_code=400,
            detail="Provide either series_key or tournament_contains/slug_contains to select a series.",
        )
    store = SQLiteStore(_get_store_path())
    try:
        matches = store.find_series_keys(
            state=state,
            videogame_id=videogame_id,
            months_back=months_back,
            window_offset=window_offset,
            window_size=window_size,
            name_contains=name_term,
            slug_contains=slug_term,
            limit=20,
        )
    finally:
        store.close()
    if not matches:
        raise HTTPException(status_code=404, detail="No precomputed series matched the provided terms.")
    if len(matches) > 1 and not allow_multi:
        options = [m["series_key"] for m in matches]
        raise HTTPException(
            status_code=412,
            detail=f"Multiple precomputed series match those terms. Specify one via series_key. Options: {options}",
        )
    return [
        {
            "series_key": match["series_key"],
            "series_name_term": match.get("series_name_term"),
            "series_slug_term": match.get("series_slug_term"),
        }
        for match in matches
    ]


def _require_columns(df: pd.DataFrame, required: Dict[str, str]) -> None:
    """Ensure the requested filters can run with the provided DataFrame."""
    missing = [pretty for column, pretty in required.items() if column not in df.columns]
    if missing:
        readable = ", ".join(missing)
        raise HTTPException(
            status_code=412,
            detail=(
                "Precomputed metrics are missing the columns required for the requested "
                f"filters ({readable}). Re-run precompute_metrics.py with the latest code "
                "so the player_metrics table persists those fields."
            ),
        )


@app.middleware("http")
async def enforce_rate_limit(request: Request, call_next):
    identity = request.client.host if request.client else "unknown"
    retry_after = await rate_limiter.check(identity)
    if retry_after is not None:
        headers = {"Retry-After": str(int(retry_after) + 1)}
        detail = (
            "Rate limit exceeded. "
            f"The API allows {rate_limiter.describe()}. Please wait and try again."
        )
        return JSONResponse(
            status_code=429,
            content={"detail": detail},
            headers=headers,
        )
    return await call_next(request)


@app.get("/health")
def health() -> Dict[str, bool]:
    """Simple liveness endpoint."""
    return {"ok": True}


@app.get("/precomputed")
def precomputed_metrics(
    state: str = Query(..., description="Two-letter region/state code."),
    months_back: int = Query(
        6,
        ge=1,
        le=24,
        description="Rolling window the metrics were generated with.",
    ),
    videogame_id: int = Query(
        1386,
        description="start.gg videogame identifier (Ultimate = 1386, Melee = 1).",
    ),
    character: str = Query(
        "Marth",
        description="Character emphasis used when precomputing metrics.",
    ),
    limit: int = Query(
        50,
        ge=0,
        le=500,
        description="Maximum number of player rows to return (0 = all).",
    ),
    filter_state: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that keeps players whose home_state matches one of the provided codes.",
    ),
    min_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Minimum average event entrants.",
    ),
    max_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Maximum average event entrants.",
    ),
    min_max_event_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Minimum entrants for a player's largest single event.",
    ),
    min_large_event_share: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of events that meet the large-event threshold.",
    ),
    start_after: Optional[str] = Query(
        None,
        description="Only include players whose latest event started on or after this date (YYYY-MM-DD).",
    ),
) -> Dict[str, Any]:
    """Serve precomputed weighted win rate/opponent strength rows from SQLite."""
    filters_requested = any(
        [
            bool(filter_state),
            min_entrants is not None,
            max_entrants is not None,
            min_max_event_entrants is not None,
            min_large_event_share is not None,
            start_after is not None,
        ]
    )
    store_limit = None if (limit == 0 or filters_requested) else limit
    rows = _load_precomputed_metrics(
        state=state,
        months_back=months_back,
        videogame_id=videogame_id,
        target_character=character,
        limit=store_limit,
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No precomputed metrics found for the requested parameters.",
        )
    df = pd.DataFrame(rows)
    start_after_ts = _parse_start_after_timestamp(start_after)
    required_columns: Dict[str, str] = {}
    if filter_state:
        required_columns["home_state"] = "home_state"
    if min_entrants is not None or max_entrants is not None:
        required_columns["avg_event_entrants"] = "avg_event_entrants"
    if min_max_event_entrants is not None:
        required_columns["max_event_entrants"] = "max_event_entrants"
    if min_large_event_share is not None:
        required_columns["large_event_share"] = "large_event_share"
    if start_after_ts is not None:
        required_columns["latest_event_start"] = "latest_event_start"
    if required_columns:
        _require_columns(df, required_columns)
    df = _apply_common_filters(
        df,
        filter_state=filter_state,
        min_entrants=min_entrants,
        max_entrants=max_entrants,
        min_max_event_entrants=min_max_event_entrants,
        min_large_event_share=min_large_event_share,
        start_after_ts=start_after_ts,
    )

    limited_df = df if limit == 0 else df.head(limit)
    records: List[Dict[str, Any]] = limited_df.to_dict(orient="records")
    return {
        "state": state,
        "character": character,
        "months_back": months_back,
        "videogame_id": videogame_id,
        "count": len(records),
        "results": records,
    }


@app.get("/search")
def search(
    state: str = Query(..., description="Two-letter region/state code."),
    character: str = Query("Marth", description="Character to emphasise in the metrics."),
    months_back: int = Query(
        6,
        ge=1,
        le=24,
        description="How many months of tournaments to include.",
    ),
    videogame_id: int = Query(
        1386,
        description="start.gg videogame identifier (Ultimate = 1386, Melee = 1).",
    ),
    window_offset: int = Query(
        0,
        ge=0,
        description="Shift the discovery window this many months into the past (0 = newest window).",
    ),
    window_size: Optional[int] = Query(
        None,
        ge=1,
        le=24,
        description="Override the window size in months (defaults to months_back).",
    ),
    assume_target_main: bool = Query(
        False,
        description="Treat the target character as a main when set data is missing.",
    ),
    large_event_threshold: int = Query(
        32,
        ge=1,
        description="Entrant count that defines a 'large' event for share filters.",
    ),
    limit: int = Query(
        25,
        ge=0,
        le=200,
        description="Maximum number of player records to return (0 = all).",
    ),
    filter_state: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that keeps players whose home_state matches one of the provided codes.",
    ),
    min_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Minimum average event entrants.",
    ),
    max_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Maximum average event entrants.",
    ),
    min_max_event_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Minimum entrants for a player's largest single event.",
    ),
    min_large_event_share: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of events that meet the large-event threshold.",
    ),
    start_after: Optional[str] = Query(
        None,
        description="Only include players whose latest event started on or after this date (YYYY-MM-DD).",
    ),
    tournament_contains: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that keeps tournaments whose name contains one of these substrings.",
    ),
    tournament_slug_contains: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that keeps tournaments whose slug contains one of these substrings.",
    ),
    tournament_slug: Optional[List[str]] = Query(
        None,
        description="Repeatable filter for exact slug match (e.g., 'tournament/genesis-9').",
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start of date range for tournaments (YYYY-MM-DD). Overrides months_back calculation.",
    ),
    end_date: Optional[str] = Query(
        None,
        description="End of date range for tournaments (YYYY-MM-DD). Overrides months_back calculation.",
    ),
) -> Dict[str, Any]:
    """
    Run the analytics pipeline and return a table of player metrics suitable for display.
    """
    token = os.getenv("STARTGG_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Missing STARTGG_API_TOKEN")

    # Parse date range if provided
    start_ts = None
    end_ts = None
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            start_ts = int(start_dt.timestamp())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid start_date '{start_date}'. Expected YYYY-MM-DD.",
            ) from exc
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
            end_ts = int(end_dt.timestamp())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid end_date '{end_date}'. Expected YYYY-MM-DD.",
            ) from exc

    try:
        df = generate_player_metrics(
            state=state,
            months_back=months_back,
            videogame_id=videogame_id,
            target_character=character,
            assume_target_main=assume_target_main,
            store_path=_get_store_path(),
            large_event_threshold=large_event_threshold,
            window_offset_months=window_offset,
            window_size_months=window_size,
            tournament_name_contains=_normalize_terms(tournament_contains),
            tournament_slug_contains=_normalize_terms(tournament_slug_contains),
            tournament_slug_exact=_normalize_terms(tournament_slug),
            start_date_override=start_ts,
            end_date_override=end_ts,
        )
    except Exception as exc:  # pragma: no cover - protective circuit
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    start_after_ts = _parse_start_after_timestamp(start_after)
    df = _apply_common_filters(
        df,
        filter_state=filter_state,
        min_entrants=min_entrants,
        max_entrants=max_entrants,
        min_max_event_entrants=min_max_event_entrants,
        min_large_event_share=min_large_event_share,
        start_after_ts=start_after_ts,
    )

    limited_df = df if limit == 0 else df.head(limit)
    records: List[Dict[str, Any]] = limited_df.to_dict(orient="records")
    return {
        "state": state,
        "character": character,
        "count": len(records),
        "results": records,
    }


@app.get("/tournaments")
def list_tournaments(
    state: str = Query(..., description="Two-letter region/state code."),
    months_back: int = Query(
        6,
        ge=1,
        le=24,
        description="How many months of tournaments to include.",
    ),
    videogame_id: int = Query(
        1386,
        description="start.gg videogame identifier (Ultimate = 1386, Melee = 1).",
    ),
    window_offset: int = Query(
        0,
        ge=0,
        description="Shift the discovery window this many months into the past (0 = newest window).",
    ),
    window_size: Optional[int] = Query(
        None,
        ge=1,
        le=24,
        description="Override the window size in months (defaults to months_back).",
    ),
    limit: int = Query(
        50,
        ge=0,
        le=500,
        description="Maximum number of tournament rows to return (0 = all).",
    ),
    tournament_contains: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that keeps tournaments whose name contains one of these substrings.",
    ),
    tournament_slug_contains: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that keeps tournaments whose slug contains one of these substrings.",
    ),
    tournament_slug: Optional[List[str]] = Query(
        None,
        description="Repeatable filter for exact slug match (e.g., 'tournament/genesis-9').",
    ),
    start_date: Optional[str] = Query(
        None,
        description="Start of date range for tournaments (YYYY-MM-DD). Overrides months_back calculation.",
    ),
    end_date: Optional[str] = Query(
        None,
        description="End of date range for tournaments (YYYY-MM-DD). Overrides months_back calculation.",
    ),
) -> Dict[str, Any]:
    """Return tournaments in the requested window, optionally filtered by series name/slug."""
    token = os.getenv("STARTGG_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Missing STARTGG_API_TOKEN")

    # Parse date range if provided
    start_ts = None
    end_ts = None
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            start_ts = int(start_dt.timestamp())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid start_date '{start_date}'. Expected YYYY-MM-DD.",
            ) from exc
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
            end_ts = int(end_dt.timestamp())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid end_date '{end_date}'. Expected YYYY-MM-DD.",
            ) from exc

    try:
        tournaments = find_tournaments(
            state=state,
            months_back=months_back,
            videogame_id=videogame_id,
            store_path=_get_store_path(),
            window_offset_months=window_offset,
            window_size_months=window_size,
            tournament_name_contains=_normalize_terms(tournament_contains),
            tournament_slug_contains=_normalize_terms(tournament_slug_contains),
            tournament_slug_exact=_normalize_terms(tournament_slug),
            start_date_override=start_ts,
            end_date_override=end_ts,
        )
    except Exception as exc:  # pragma: no cover - protective circuit
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    limited = tournaments if limit == 0 else tournaments[:limit]
    records = []
    for tourney in limited:
        records.append(
            {
                "id": tourney.get("id"),
                "slug": tourney.get("slug"),
                "name": tourney.get("name"),
                "city": tourney.get("city"),
                "state": tourney.get("addrState") or tourney.get("state"),
                "country": tourney.get("addrCountry"),
                "start_at": tourney.get("startAt"),
                "end_at": tourney.get("endAt"),
                "num_attendees": tourney.get("numAttendees"),
                "name_matches": tourney.get("_name_matches") or [],
                "slug_matches": tourney.get("_slug_matches") or [],
            }
        )

    return {
        "state": state,
        "videogame_id": videogame_id,
        "months_back": months_back,
        "count": len(records),
        "results": records,
    }


@app.get("/tournaments/by-slug")
def list_tournaments_by_slug(
    tournament_slug: List[str] = Query(
        ...,
        description="Repeatable exact slug or start.gg URL (e.g., 'tournament/genesis-9').",
    ),
) -> Dict[str, Any]:
    """Return one or more tournaments by slug without requiring a state window."""
    token = os.getenv("STARTGG_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Missing STARTGG_API_TOKEN")

    slugs = []
    invalid_inputs = []
    for raw in tournament_slug:
        slug = _extract_tournament_slug(raw)
        if slug:
            slugs.append(slug)
        else:
            invalid_inputs.append(raw)

    if not slugs:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one tournament_slug or start.gg tournament URL.",
        )

    # Preserve order while deduping to avoid redundant network calls.
    seen = set()
    normalized_slugs = []
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        normalized_slugs.append(slug)

    client = StartGGClient()
    records = []
    missing = []
    for slug in normalized_slugs:
        try:
            tourney = client.fetch_tournament_by_slug(slug)
        except Exception as exc:  # pragma: no cover - protective circuit
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if not tourney:
            missing.append(slug)
            continue

        records.append(
            {
                "id": tourney.get("id"),
                "slug": tourney.get("slug"),
                "name": tourney.get("name"),
                "city": tourney.get("city"),
                "state": tourney.get("addrState") or tourney.get("state"),
                "country": tourney.get("countryCode"),
                "start_at": tourney.get("startAt"),
                "end_at": tourney.get("endAt"),
                "num_attendees": tourney.get("numAttendees"),
            }
        )

    return {
        "count": len(records),
        "results": records,
        "missing": missing,
        "invalid": invalid_inputs,
    }


@app.get("/search/by-slug")
def search_by_slug(
    tournament_slug: List[str] = Query(
        ...,
        description="Repeatable exact slug or start.gg URL (e.g., 'tournament/genesis-9').",
    ),
    character: str = Query(
        "Marth",
        description="Character to emphasise in the metrics (same semantics as /search).",
    ),
    videogame_id: int = Query(
        1386,
        description="start.gg videogame identifier (Ultimate = 1386, Melee = 1).",
    ),
    assume_target_main: bool = Query(
        False,
        description="Treat the target character as a main when set data is missing.",
    ),
    large_event_threshold: int = Query(
        32,
        ge=1,
        description="Entrant count that defines a 'large' event for share filters.",
    ),
    limit: int = Query(
        25,
        ge=0,
        le=200,
        description="Maximum number of player records to return (0 = all).",
    ),
    filter_state: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that keeps players whose home_state matches one of the provided codes.",
    ),
    min_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Minimum average event entrants.",
    ),
    max_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Maximum average event entrants.",
    ),
    min_max_event_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Minimum entrants for a player's largest single event.",
    ),
    min_large_event_share: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of events that meet the large-event threshold.",
    ),
    start_after: Optional[str] = Query(
        None,
        description="Only include players whose latest event started on or after this date (YYYY-MM-DD).",
    ),
    refresh: bool = Query(
        False,
        description="Bypass cached SQLite data and refetch tournament/events/sets from start.gg.",
    ),
    debug: bool = Query(
        False,
        description="When true, include diagnostic information about discovered events.",
    ),
) -> Dict[str, Any]:
    """
    Compute player metrics for one or more tournaments addressed by slug only.
    Mirrors /search filters but skips state/month windows.
    """
    token = os.getenv("STARTGG_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Missing STARTGG_API_TOKEN")

    slugs = []
    invalid_inputs = []
    for raw in tournament_slug:
        slug = _extract_tournament_slug(raw)
        if slug:
            slugs.append(slug)
        else:
            invalid_inputs.append(raw)

    if not slugs:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one tournament_slug or start.gg tournament URL.",
        )

    start_after_ts = _parse_start_after_timestamp(start_after)

    debug_info: List[Dict[str, Any]] = []
    if debug:
        dbg_client = StartGGClient()
        dbg_store = None if refresh else SQLiteStore(_get_store_path())
        try:
            for slug in slugs:
                tourney = dbg_client.fetch_tournament_by_slug(slug)
                if not tourney:
                    debug_info.append(
                        {"slug": slug, "error": "tournament not found from start.gg"}
                    )
                    continue
                tournament_id = tourney.get("id")
                if tournament_id is None:
                    debug_info.append(
                        {"slug": slug, "error": "tournament missing id from start.gg payload"}
                    )
                    continue
                events = fetch_tournament_events(
                    dbg_client,
                    tournament_id=int(tournament_id),
                    store=dbg_store,
                )
                event_summaries = []
                for ev in events:
                    vg = (ev.get("videogame") or {}).get("id")
                    event_summaries.append(
                        {
                            "id": ev.get("id"),
                            "name": ev.get("name"),
                            "slug": ev.get("slug"),
                            "videogame_id": vg,
                            "is_singles": is_singles_event(ev),
                            "num_entrants": ev.get("numEntrants"),
                        }
                    )
                debug_info.append(
                    {
                        "slug": slug,
                        "tournament_id": tournament_id,
                        "event_count": len(events),
                        "events": event_summaries,
                    }
                )
        finally:
            if dbg_store is not None:
                dbg_store.close()

    try:
        df = generate_player_metrics_for_tournaments(
            tournament_slugs=slugs,
            videogame_id=videogame_id,
            target_character=character,
            assume_target_main=assume_target_main,
            use_store=not refresh,
            store_path=_get_store_path(),
            large_event_threshold=large_event_threshold,
        )
    except Exception as exc:  # pragma: no cover - protective circuit
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    df = _apply_common_filters(
        df,
        filter_state=filter_state,
        min_entrants=min_entrants,
        max_entrants=max_entrants,
        min_max_event_entrants=min_max_event_entrants,
        min_large_event_share=min_large_event_share,
        start_after_ts=start_after_ts,
    )

    limited_df = df if limit == 0 else df.head(limit)
    records: List[Dict[str, Any]] = limited_df.to_dict(orient="records")
    response: Dict[str, Any] = {
        "slugs": slugs,
        "character": character,
        "count": len(records),
        "results": records,
        "invalid": invalid_inputs,
    }
    if debug:
        response["debug"] = debug_info
    return response


@app.get("/precomputed_series")
def precomputed_series(
    state: str = Query(..., description="Two-letter region/state code."),
    months_back: int = Query(
        6,
        ge=1,
        le=24,
        description="Rolling window the metrics were generated with.",
    ),
    videogame_id: int = Query(
        1386,
        description="start.gg videogame identifier (Ultimate = 1386, Melee = 1).",
    ),
    window_offset: int = Query(
        0,
        ge=0,
        description="Shift the window this many months into the past (0 = newest window).",
    ),
    window_size: Optional[int] = Query(
        None,
        ge=1,
        le=24,
        description="Override the window size in months (defaults to months_back).",
    ),
    series_key: Optional[str] = Query(
        None,
        description="Exact series key to load. Skip this if you prefer term-based selection.",
    ),
    tournament_contains: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that matches series whose name term contains this substring.",
    ),
    tournament_slug_contains: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that matches series whose slug term contains this substring.",
    ),
    allow_multi: bool = Query(
        False,
        description="When true, return all precomputed series matching the terms instead of requiring a single match.",
    ),
    limit: int = Query(
        50,
        ge=0,
        le=500,
        description="Maximum number of player rows to return (0 = all).",
    ),
    filter_state: Optional[List[str]] = Query(
        None,
        description="Repeatable filter that keeps players whose home_state matches one of the provided codes.",
    ),
    min_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Minimum average event entrants.",
    ),
    max_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Maximum average event entrants.",
    ),
    min_max_event_entrants: Optional[int] = Query(
        None,
        ge=0,
        description="Minimum entrants for a player's largest single event.",
    ),
    min_large_event_share: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of events that meet the large-event threshold.",
    ),
    start_after: Optional[str] = Query(
        None,
        description="Only include players whose latest event started on or after this date (YYYY-MM-DD).",
    ),
) -> Dict[str, Any]:
    """Serve precomputed series-scoped metrics from SQLite."""
    resolved_matches = _find_series(
        state=state,
        videogame_id=videogame_id,
        months_back=months_back,
        window_offset=window_offset,
        window_size=window_size,
        series_key=series_key,
        tournament_contains=_normalize_terms(tournament_contains),
        tournament_slug_contains=_normalize_terms(tournament_slug_contains),
        allow_multi=allow_multi,
    )

    filters_requested = any(
        [
            bool(filter_state),
            min_entrants is not None,
            max_entrants is not None,
            min_max_event_entrants is not None,
            min_large_event_share is not None,
            start_after is not None,
        ]
    )
    store_limit = None if (limit == 0 or filters_requested or allow_multi) else limit

    annotated_rows: List[Dict[str, Any]] = []
    for resolved in resolved_matches:
        rows = _load_series_metrics(
            state=state,
            months_back=months_back,
            videogame_id=videogame_id,
            window_offset=window_offset,
            window_size=window_size,
            series_key=resolved["series_key"],
            limit=store_limit,
        )
        for row in rows:
            row = dict(row)
            row["series_key"] = resolved["series_key"]
            row["series_name_term"] = resolved.get("series_name_term")
            row["series_slug_term"] = resolved.get("series_slug_term")
            row["series_label"] = (
                resolved.get("series_name_term")
                or resolved.get("series_slug_term")
                or resolved["series_key"]
            )
            annotated_rows.append(row)

    if not annotated_rows:
        raise HTTPException(
            status_code=404,
            detail="No precomputed series metrics found for the requested parameters.",
        )

    df = pd.DataFrame(annotated_rows)
    start_after_ts = _parse_start_after_timestamp(start_after)
    required_columns: Dict[str, str] = {}
    if filter_state:
        required_columns["home_state"] = "home_state"
    if min_entrants is not None or max_entrants is not None:
        required_columns["avg_event_entrants"] = "avg_event_entrants"
    if min_max_event_entrants is not None:
        required_columns["max_event_entrants"] = "max_event_entrants"
    if min_large_event_share is not None:
        required_columns["large_event_share"] = "large_event_share"
    if start_after_ts is not None:
        required_columns["latest_event_start"] = "latest_event_start"
    if required_columns:
        _require_columns(df, required_columns)

    df = _apply_common_filters(
        df,
        filter_state=filter_state,
        min_entrants=min_entrants,
        max_entrants=max_entrants,
        min_max_event_entrants=min_max_event_entrants,
        min_large_event_share=min_large_event_share,
        start_after_ts=start_after_ts,
    )

    limited_df = df if limit == 0 else df.head(limit)
    records: List[Dict[str, Any]] = limited_df.to_dict(orient="records")
    response: Dict[str, Any] = {
        "state": state,
        "months_back": months_back,
        "videogame_id": videogame_id,
        "count": len(records),
        "results": records,
    }

    if allow_multi and len(resolved_matches) > 1:
        response["series_keys"] = [m["series_key"] for m in resolved_matches]
        response["resolved_labels"] = [
            m.get("series_name_term") or m.get("series_slug_term") or m["series_key"]
            for m in resolved_matches
        ]
    else:
        resolved = resolved_matches[0]
        response.update(
            {
                "series_key": resolved["series_key"],
                "series_name_term": resolved.get("series_name_term"),
                "series_slug_term": resolved.get("series_slug_term"),
                "resolved_label": resolved.get("series_name_term")
                or resolved.get("series_slug_term")
                or resolved["series_key"],
            }
        )

    return response
