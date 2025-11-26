"""High-level analytics entry points built on top of the start.gg helpers."""

from pathlib import Path
from typing import Optional

import pandas as pd

from .datastore import SQLiteStore
from .metrics import compute_player_metrics
from .smash_data import (
    TournamentFilter,
    collect_player_results_for_tournaments,
    fetch_recent_tournaments,
)
from .series import SeriesCandidate, rank_series_for_state
from .startgg_client import StartGGClient


def generate_player_metrics(
    state: str = "GA",
    months_back: int = 6,
    videogame_id: int = 1386,
    target_character: str = "Marth",
    use_cache: bool = False,
    assume_target_main: bool = False,
    use_store: bool = True,
    store_path: Optional[Path] = None,
    large_event_threshold: int = 32,
    window_offset_months: int = 0,
    window_size_months: Optional[int] = None,
    tournament_name_contains: Optional[list[str]] = None,
    tournament_slug_contains: Optional[list[str]] = None,
    suppress_logs: bool = False,
) -> pd.DataFrame:
    """
    Run the full data pipeline and return a DataFrame with per-player metrics.

    Parameters
    ----------
    state:
        Two-letter state code (defaults to Georgia).
    months_back:
        Number of months to look back when discovering tournaments.
    videogame_id:
        start.gg videogame identifier (1386 == Super Smash Bros. Ultimate).
    target_character:
        Character name to derive character-specific metrics (default "Marth").
    use_cache:
        Whether to persist raw GraphQL responses as JSON. When `use_store` is True
        this setting is ignored (we rely on the SQLite database instead).
    use_store:
        When True, persist tournaments/events inside a SQLite database so follow-up
        runs can be served offline. Disable for ephemeral environments.
    large_event_threshold:
        Entrant count used to flag “large” events when computing large_event_share.
    """
    client_use_cache = use_cache and not use_store
    client = StartGGClient(use_cache=client_use_cache)
    store: Optional[SQLiteStore] = SQLiteStore(store_path) if use_store else None
    filt = TournamentFilter(
        state=state,
        videogame_id=videogame_id,
        months_back=months_back,
        window_offset=window_offset_months,
        window_size=window_size_months,
        name_contains=tuple(tournament_name_contains or ()),
        slug_contains=tuple(tournament_slug_contains or ()),
    )
    try:
        tournaments = fetch_recent_tournaments(client, filt, store=store, suppress_logs=suppress_logs)
        player_results = collect_player_results_for_tournaments(
            client,
            tournaments,
            target_videogame_id=videogame_id,
            store=store,
        )
    finally:
        if store is not None:
            store.close()
    return compute_player_metrics(
        player_results,
        target_character=target_character,
        assume_target_main=assume_target_main,
        large_event_threshold=large_event_threshold,
    )


def generate_character_report(
    state: str = "GA",
    character: Optional[str] = "Marth",
    months_back: int = 6,
    videogame_id: int = 1386,
    use_cache: bool = False,
    assume_target_main: bool = False,
    use_store: bool = True,
    store_path: Optional[Path] = None,
    large_event_threshold: int = 32,
    window_offset_months: int = 0,
    window_size_months: Optional[int] = None,
    tournament_name_contains: Optional[list[str]] = None,
    tournament_slug_contains: Optional[list[str]] = None,
    suppress_logs: bool = False,
) -> pd.DataFrame:
    """
    Backwards-compatible wrapper that filters the metrics DataFrame to players
    who primarily use the requested character.
    """
    df = generate_player_metrics(
        state=state,
        months_back=months_back,
        videogame_id=videogame_id,
        target_character=character or "Marth",
        use_cache=use_cache,
        assume_target_main=assume_target_main,
        use_store=use_store,
        store_path=store_path,
        large_event_threshold=large_event_threshold,
        window_offset_months=window_offset_months,
        window_size_months=window_size_months,
        tournament_name_contains=tournament_name_contains,
        tournament_slug_contains=tournament_slug_contains,
        suppress_logs=suppress_logs,
    )
    if df.empty or character is None:
        return df

    mask = df["character_usage_rate"] > 0
    # Only keep players who actually logged sets with the requested character.
    filtered = df[mask].copy()
    filtered.reset_index(drop=True, inplace=True)
    filtered.rename(
        columns={
            "character_sets": f"{character}_sets",
            "character_win_rate": f"{character}_win_rate",
            "character_weighted_win_rate": f"{character}_weighted_win_rate",
            "character_usage_rate": f"{character}_usage_rate",
        },
        inplace=True,
    )
    return filtered


def precompute_state_metrics(
    state: str,
    *,
    months_back: int = 6,
    videogame_id: int = 1386,
    target_character: str = "Marth",
    assume_target_main: bool = False,
    store_path: Optional[Path] = None,
    large_event_threshold: int = 32,
    window_offset_months: int = 0,
    window_size_months: Optional[int] = None,
) -> int:
    """
    Compute metrics for a single state and persist weighted win rate/opponent strength.

    Returns the number of player rows written to the store.
    """
    df = generate_player_metrics(
        state=state,
        months_back=months_back,
        videogame_id=videogame_id,
        target_character=target_character,
        assume_target_main=assume_target_main,
        store_path=store_path,
        large_event_threshold=large_event_threshold,
        window_offset_months=window_offset_months,
        window_size_months=window_size_months,
    )
    if df.empty:
        return 0

    persisted_columns = [
        "player_id",
        "gamer_tag",
        "weighted_win_rate",
        "opponent_strength",
        "home_state",
        "home_state_inferred",
        "avg_event_entrants",
        "max_event_entrants",
        "large_event_share",
        "latest_event_start",
    ]
    records = df[persisted_columns].to_dict(orient="records")
    store = SQLiteStore(store_path)
    try:
        store.replace_player_metrics(
            state=state,
            videogame_id=videogame_id,
            months_back=months_back,
            target_character=target_character,
            rows=records,
        )
    finally:
        store.close()
    return len(records)


def precompute_series_metrics(
    *,
    state: str,
    series_key: str,
    series_name_term: Optional[str],
    series_slug_term: Optional[str],
    months_back: int = 6,
    videogame_id: int = 1386,
    target_character: str = "Marth",
    assume_target_main: bool = False,
    store_path: Optional[Path] = None,
    large_event_threshold: int = 32,
    window_offset_months: int = 0,
    window_size_months: Optional[int] = None,
) -> int:
    """Compute metrics for a specific series and persist them."""
    df = generate_player_metrics(
        state=state,
        months_back=months_back,
        videogame_id=videogame_id,
        target_character=target_character,
        assume_target_main=assume_target_main,
        store_path=store_path,
        large_event_threshold=large_event_threshold,
        window_offset_months=window_offset_months,
        window_size_months=window_size_months,
        tournament_name_contains=[series_name_term] if series_name_term else None,
        tournament_slug_contains=[series_slug_term] if series_slug_term else None,
        suppress_logs=True,
    )
    if df.empty:
        return 0

    persisted_columns = [
        "player_id",
        "gamer_tag",
        "weighted_win_rate",
        "opponent_strength",
        "home_state",
        "home_state_inferred",
        "avg_event_entrants",
        "max_event_entrants",
        "large_event_share",
        "latest_event_start",
    ]
    records = df[persisted_columns].to_dict(orient="records")
    store = SQLiteStore(store_path)
    try:
        store.replace_series_metrics(
            state=state,
            videogame_id=videogame_id,
            months_back=months_back,
            window_offset=window_offset_months,
            window_size=window_size_months,
            series_key=series_key,
            series_name_term=series_name_term,
            series_slug_term=series_slug_term,
            rows=records,
        )
    finally:
        store.close()
    return len(records)


def auto_select_series(
    *,
    state: str,
    months_back: int = 6,
    videogame_id: int = 1386,
    window_offset_months: int = 0,
    window_size_months: Optional[int] = None,
    store_path: Optional[Path] = None,
    top_n: int = 25,
    min_max_attendees: int = 0,
    min_event_count: int = 0,
) -> list[SeriesCandidate]:
    """Return ranked series candidates for a state using cached tournaments."""
    return rank_series_for_state(
        state=state,
        months_back=months_back,
        videogame_id=videogame_id,
        window_offset=window_offset_months,
        window_size=window_size_months,
        store_path=store_path,
        top_n=top_n,
        min_max_attendees=min_max_attendees,
        min_event_count=min_event_count,
    )


def find_tournaments(
    state: str = "GA",
    months_back: int = 6,
    videogame_id: int = 1386,
    *,
    use_store: bool = True,
    store_path: Optional[Path] = None,
    window_offset_months: int = 0,
    window_size_months: Optional[int] = None,
    tournament_name_contains: Optional[list[str]] = None,
    tournament_slug_contains: Optional[list[str]] = None,
) -> list[dict]:
    """
    Discover tournaments within the requested window, optionally filtered by name/slug substrings.
    """
    client = StartGGClient(use_cache=not use_store)
    store: Optional[SQLiteStore] = SQLiteStore(store_path) if use_store else None
    filt = TournamentFilter(
        state=state,
        videogame_id=videogame_id,
        months_back=months_back,
        window_offset=window_offset_months,
        window_size=window_size_months,
        name_contains=tuple(tournament_name_contains or ()),
        slug_contains=tuple(tournament_slug_contains or ()),
    )
    try:
        tournaments = fetch_recent_tournaments(client, filt, store=store)
    finally:
        if store is not None:
            store.close()
    return tournaments


__all__ = [
    "generate_player_metrics",
    "generate_character_report",
    "precompute_state_metrics",
    "precompute_series_metrics",
    "auto_select_series",
    "rank_series_for_state",
    "find_tournaments",
]
