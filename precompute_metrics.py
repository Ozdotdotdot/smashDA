#!/usr/bin/env python3
"""CLI helper to persist precomputed player metrics for one or more states."""

import argparse
import os
from pathlib import Path
from typing import List

from smashcc.analysis import precompute_state_metrics
from smashcc.datastore import SQLiteStore


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
    args = parser.parse_args()

    if not args.states and not args.all_states:
        parser.error("provide at least one --state or pass --all-states")

    if not os.getenv("STARTGG_API_TOKEN"):
        parser.error("STARTGG_API_TOKEN is not set; export it before running.")

    store_path = Path(args.store_path) if args.store_path else None
    states = _resolve_states(
        states=args.states,
        include_all=args.all_states,
        videogame_id=args.videogame_id,
        store_path=store_path or Path(".cache") / "startgg" / "smash.db",
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
        )
        print(f"    Stored {row_count} players for {state}.")
        processed += 1

    print(f"Finished processing {processed} state(s).")


if __name__ == "__main__":
    main()
