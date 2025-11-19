"""FastAPI entrypoint exposing the Smash Character Competency analytics."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query

from smashcc.analysis import generate_player_metrics
from smashcc.datastore import SQLiteStore

app = FastAPI(
    title="Smash Character Competency API",
    description="Lightweight API exposing player metrics derived from start.gg data.",
    version="0.1.0",
)

DEFAULT_STORE_PATH = Path(".cache") / "startgg" / "smash.db"


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
) -> Dict[str, Any]:
    """
    Run the analytics pipeline and return a table of player metrics suitable for display.
    """
    token = os.getenv("STARTGG_API_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Missing STARTGG_API_TOKEN")

    try:
        df = generate_player_metrics(
            state=state,
            months_back=months_back,
            videogame_id=videogame_id,
            target_character=character,
            assume_target_main=assume_target_main,
            store_path=_get_store_path(),
            large_event_threshold=large_event_threshold,
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
