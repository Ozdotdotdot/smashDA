"""Helpers for discovering and ranking tournament series candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .datastore import SQLiteStore
from .smash_data import fetch_recent_tournaments, fetch_tournament_events, is_singles_event
from .startgg_client import StartGGClient, TournamentFilter


@dataclass(frozen=True)
class SeriesCandidate:
    """Represents a single tournament series candidate for precomputation."""

    series_key: str
    name_term: Optional[str]
    slug_term: Optional[str]
    sample_name: Optional[str]
    sample_slug: Optional[str]
    tournaments: List[int]
    event_count: int
    total_attendees: int
    max_attendees: int


def _normalize_slug_token(slug: Optional[str]) -> Optional[str]:
    """Return a normalized slug segment without trailing volume numbers."""
    if not slug:
        return None
    token = slug.split("/")[-1].lower()
    token = re.sub(r"-?(week|wk|weekly|monthly|month|vol|volume)?-?\d+$", "", token)
    token = re.sub(r"-+", "-", token).strip("-")
    return token or None


def _normalize_name_token(name: Optional[str]) -> Optional[str]:
    """Normalize a tournament name into a substring-friendly token."""
    if not name:
        return None
    token = name.lower().strip()
    token = re.sub(r"\s+", " ", token)
    token = re.sub(r"(week|wk|weekly|monthly|month|vol|volume)\s*\d+$", "", token)
    token = token.strip(" -")
    return token or None


def rank_series_for_state(
    *,
    state: str,
    months_back: int = 6,
    videogame_id: int = 1386,
    window_offset: int = 0,
    window_size: Optional[int] = None,
    store_path: Optional[Path] = None,
    top_n: int = 5,
    min_max_attendees: int = 32,
    min_event_count: int = 3,
    use_cache: bool = True,
) -> List[SeriesCandidate]:
    """
    Analyze tournaments in the cached window and return ranked series candidates.

    Selection logic:
        * Compute per-series totals for singles events matching the videogame_id
          (using event.numEntrants).
        * Include the top N by total attendees.
        * Also include any series whose max entrants >= min_max_attendees
          OR whose event count >= min_event_count.
    """
    client = StartGGClient(use_cache=use_cache)
    store: Optional[SQLiteStore] = SQLiteStore(store_path)
    filt = TournamentFilter(
        state=state,
        videogame_id=videogame_id,
        months_back=months_back,
        window_offset=window_offset,
        window_size=window_size,
    )
    tournaments = fetch_recent_tournaments(client, filt, store=store)

    series_map: Dict[str, Dict] = {}
    for tourney in tournaments:
        tid = tourney.get("id")
        if tid is None:
            continue
        name_term = _normalize_name_token(tourney.get("name"))
        slug_term = _normalize_slug_token(tourney.get("slug"))
        series_key = slug_term or name_term
        if series_key is None:
            series_key = str(tid)
        # Load events to derive entrant counts for the target game singles events.
        events = store.load_events(tid) if store is not None else []
        if not events:
            events = fetch_tournament_events(client, int(tid), store=store)
        matching_events = [
            e
            for e in events
            if is_singles_event(e) and str((e.get("videogame") or {}).get("id")) == str(videogame_id)
        ]
        if not matching_events:
            continue
        total_entrants = 0
        max_entrants = 0
        for event in matching_events:
            entrants = int(event.get("numEntrants") or 0)
            total_entrants += entrants
            max_entrants = max(max_entrants, entrants)

        entry = series_map.setdefault(
            series_key,
            {
                "name_term": name_term,
                "slug_term": slug_term,
                "sample_name": tourney.get("name"),
                "sample_slug": tourney.get("slug"),
                "tournaments": [],
                "event_count": 0,
                "total_attendees": 0,
                "max_attendees": 0,
            },
        )
        entry["tournaments"].append(int(tid))
        entry["event_count"] += len(matching_events)
        entry["total_attendees"] += total_entrants
        entry["max_attendees"] = max(entry["max_attendees"], max_entrants)

    candidates: List[SeriesCandidate] = []
    for key, data in series_map.items():
        candidates.append(
            SeriesCandidate(
                series_key=key,
                name_term=data["name_term"],
                slug_term=data["slug_term"],
                sample_name=data["sample_name"],
                sample_slug=data["sample_slug"],
                tournaments=data["tournaments"],
                event_count=data["event_count"],
                total_attendees=data["total_attendees"],
                max_attendees=data["max_attendees"],
            )
        )

    # Sort by total attendees, then event count, then max attendees.
    candidates.sort(
        key=lambda c: (c.total_attendees, c.event_count, c.max_attendees, c.series_key),
        reverse=True,
    )

    selected: Dict[str, SeriesCandidate] = {}
    for cand in candidates[: max(0, int(top_n))]:
        selected[cand.series_key] = cand

    for cand in candidates:
        if cand.series_key in selected:
            continue
        if cand.max_attendees >= max(0, int(min_max_attendees)) or cand.event_count >= max(
            1, int(min_event_count)
        ):
            selected[cand.series_key] = cand

    # Stable ordering: keep the original sort for determinism.
    result = [c for c in candidates if c.series_key in selected]
    if store is not None:
        store.close()
    return result
