#!/usr/bin/env python3
"""CLI helper to persist precomputed player metrics for one or more states."""

import argparse
import os
from pathlib import Path
from typing import List, Optional

from smashcc.analysis import (
    auto_select_series,
    precompute_series_metrics,
    precompute_state_metrics,
)
from smashcc.datastore import SQLiteStore
from smashcc.startgg_client import TournamentFilter


def _resolve_states(
    *,
    states: List[str],
    include_all: bool,
    videogame_id: int,
    store_path: Path,
) -> List[str]:
    """Return the list of states that should be processed."""
    if include_all:
        store = SQLiteStore(store_path)
        try:
            discovered = store.list_states_with_data(videogame_id)
        finally:
            store.close()
        return discovered
    normalized = [s.strip().upper() for s in states if s.strip()]
    return sorted(set(normalized))


def _derive_series_key(
    *,
    name_terms: List[str],
    slug_terms: List[str],
    override: Optional[str] = None,
) -> str:
    """Generate a deterministic series key from provided terms."""
    if override and override.strip():
        return override.strip()
    for term in [*(slug_terms or []), *(name_terms or [])]:
        cleaned = term.strip().lower().replace(" ", "-").replace("/", "-")
        if cleaned:
            while "--" in cleaned:
                cleaned = cleaned.replace("--", "-")
            return cleaned
    return "custom-series"


def _suggest_top_n_for_state(
    *,
    state: str,
    videogame_id: int,
    months_back: int,
    window_offset_months: int,
    window_size_months: Optional[int],
    store_path: Path,
    small_default: int = 25,
    medium_default: int = 75,
    large_default: int = 125,
    huge_default: int = 200,
) -> tuple[int, str]:
    """
    Pick a top-N cap based on cached tournament volume for the window.

    Returns (top_n, reason).
    """
    filt = TournamentFilter(
        state=state,
        videogame_id=videogame_id,
        months_back=months_back,
        window_offset=window_offset_months,
        window_size=window_size_months,
    )
    try:
        store = SQLiteStore(store_path)
    except Exception:
        return small_default, "fallback (no store available)"
    try:
        window_start, window_end = filt.window_bounds()
        tournaments = store.load_tournaments(
            filt.state,
            filt.videogame_id,
            window_start,
            window_end,
        )
        count = len(tournaments)
    finally:
        store.close()

    state_upper = state.upper()
    high_population_states = {"CA", "TX", "FL", "NJ", "NY"}
    if state_upper in high_population_states:
        return huge_default, f"population override ({state_upper})"
    if count >= 200:
        return huge_default, f"huge state: {count} cached tournaments"
    if count >= 120:
        return large_default, f"large state: {count} cached tournaments"
    if count >= 70:
        return medium_default, f"medium state: {count} cached tournaments"
    return small_default, f"small state: {count} cached tournaments"


def main() -> None:
    parser = argparse.ArgumentParser(description="Precompute and persist player metrics per state.")
    parser.add_argument(
        "--state",
        dest="states",
        action="append",
        default=[],
        help="State code to process (can be provided multiple times).",
    )
    parser.add_argument(
        "--all-states",
        action="store_true",
        help="Process every state that already has tournaments in the local SQLite store.",
    )
    parser.add_argument(
        "--videogame-id",
        type=int,
        default=1386,
        help="start.gg videogame identifier (Ultimate=1386, Melee=1).",
    )
    parser.add_argument(
        "--months-back",
        type=int,
        default=6,
        help="Rolling tournament window (in months) used when computing metrics.",
    )
    parser.add_argument(
        "--window-offset",
        type=int,
        default=0,
        help="Shift the window this many months into the past (0 = latest window).",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        help="Override the window length (months). Defaults to --months-back.",
    )
    parser.add_argument(
        "--character",
        default="Marth",
        help="Character to emphasise when deriving metrics.",
    )
    parser.add_argument(
        "--assume-target-main",
        action="store_true",
        help="Treat the target character as a player's main when no per-character sets exist.",
    )
    parser.add_argument(
        "--large-event-threshold",
        type=int,
        default=32,
        help="Entrant count that defines a 'large' event (affects derived metrics).",
    )
    parser.add_argument(
        "--store-path",
        default=None,
        help="Optional override for the SQLite store path.",
    )
    parser.add_argument(
        "--auto-series",
        action="store_true",
        help=(
            "Automatically select series per state and precompute series-scoped metrics "
            "(top N by total attendees; defaults dynamically to 200/125/75/25 based on cached tournament volume/state)."
        ),
    )
    parser.add_argument(
        "--top-n-per-state",
        type=int,
        default=None,
        help="How many series per state to include (largest by total attendees). Defaults dynamically by state size if omitted.",
    )
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="Use cached tournaments/events only; fail if data is missing or stale and never hit start.gg.",
    )
    parser.add_argument(
        "--tournament-contains",
        dest="tournament_contains",
        action="append",
        help=(
            "Only include tournaments whose name contains this substring (case-insensitive). "
            "Repeatable; mirrors run_report.py."
        ),
    )
    parser.add_argument(
        "--tournament-slug-contains",
        dest="tournament_slug_contains",
        action="append",
        help=(
            "Only include tournaments whose slug contains this substring (case-insensitive). "
            "Repeatable; mirrors run_report.py."
        ),
    )
    parser.add_argument(
        "--series-key",
        help=(
            "Optional override for the series key when using --tournament-contains/--tournament-slug-contains. "
            "Defaults to the first provided term."
        ),
    )
    args = parser.parse_args()

    if not args.states and not args.all_states:
        parser.error("provide at least one --state or pass --all-states")

    if not os.getenv("STARTGG_API_TOKEN") and not args.offline_only:
        parser.error("STARTGG_API_TOKEN is not set; export it before running.")

    store_path = Path(args.store_path) if args.store_path else None
    states = _resolve_states(
        states=args.states,
        include_all=args.all_states,
        videogame_id=args.videogame_id,
        store_path=store_path or Path(".cache") / "startgg" / "smash.db",
    )
    name_terms = [t.strip() for t in args.tournament_contains or [] if t and t.strip()]
    slug_terms = [t.strip() for t in args.tournament_slug_contains or [] if t and t.strip()]
    manual_series = bool(name_terms or slug_terms)
    manual_series_key = (
        _derive_series_key(
            name_terms=name_terms,
            slug_terms=slug_terms,
            override=args.series_key,
        )
        if manual_series
        else None
    )
    if not states:
        print("No states found to process.")
        return

    processed = 0
    for state in states:
        print(f"[+] Computing metrics for {state}...")
        row_count = precompute_state_metrics(
            state=state,
            months_back=args.months_back,
            videogame_id=args.videogame_id,
            target_character=args.character,
            assume_target_main=args.assume_target_main,
            store_path=store_path,
            large_event_threshold=args.large_event_threshold,
            window_offset_months=args.window_offset,
            window_size_months=args.window_size,
            offline_only=args.offline_only,
        )
        print(f"    Stored {row_count} players for {state}.")
        processed += 1
        if manual_series:
            print(
                "    Precomputing series for provided tournament filters..."
            )
            series_rows = precompute_series_metrics(
                state=state,
                series_key=manual_series_key or "custom-series",
                series_name_term=name_terms[0] if name_terms else None,
                series_slug_term=slug_terms[0] if slug_terms else None,
                tournament_name_contains=name_terms or None,
                tournament_slug_contains=slug_terms or None,
                months_back=args.months_back,
                videogame_id=args.videogame_id,
                target_character=args.character,
                assume_target_main=args.assume_target_main,
                store_path=store_path,
                large_event_threshold=args.large_event_threshold,
                window_offset_months=args.window_offset,
                window_size_months=args.window_size,
                offline_only=args.offline_only,
            )
            print(f"        Stored {series_rows} players for series '{manual_series_key}'.")
        if args.auto_series:
            resolved_top_n = args.top_n_per_state
            if resolved_top_n is None:
                suggested_top_n, reason = _suggest_top_n_for_state(
                    state=state,
                    videogame_id=args.videogame_id,
                    months_back=args.months_back,
                    window_offset_months=args.window_offset,
                    window_size_months=args.window_size,
                    store_path=store_path or Path(".cache") / "startgg" / "smash.db",
                )
                resolved_top_n = suggested_top_n
                print(f"    Auto-series top N set to {resolved_top_n} ({reason}).")
            else:
                print(f"    Auto-series top N override: {resolved_top_n}.")
            print("    Selecting series candidates...")
            candidates = auto_select_series(
                state=state,
                months_back=args.months_back,
                videogame_id=args.videogame_id,
                window_offset_months=args.window_offset,
                window_size_months=args.window_size,
                store_path=store_path,
                top_n=resolved_top_n,
                offline_only=args.offline_only,
            )
            if not candidates:
                print("    No series candidates found for this state/window.")
            for cand in candidates:
                print(
                    f"    Precomputing series '{cand.series_key}' "
                    f"(events={cand.event_count}, max={cand.max_attendees}, total={cand.total_attendees})..."
                )
                series_rows = precompute_series_metrics(
                    state=state,
                    series_key=cand.series_key,
                    series_name_term=cand.name_term,
                    series_slug_term=cand.slug_term,
                    months_back=args.months_back,
                    videogame_id=args.videogame_id,
                    target_character=args.character,
                    assume_target_main=args.assume_target_main,
                    store_path=store_path,
                    large_event_threshold=args.large_event_threshold,
                    window_offset_months=args.window_offset,
                    window_size_months=args.window_size,
                    offline_only=args.offline_only,
                )
                print(f"        Stored {series_rows} players for series '{cand.series_key}'.")

    print(f"Finished processing {processed} state(s).")


if __name__ == "__main__":
    main()
