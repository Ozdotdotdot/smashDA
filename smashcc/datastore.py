"""
datastore.py
------------

Persistence helpers for storing start.gg payloads in a local SQLite database.
The goal is to retain historical tournament/event data so notebook + CLI usage
can avoid re-downloading the same tournaments after the initial fetch.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class SQLiteStore:
    """Small helper that persists tournaments + event payloads locally."""

    def __init__(
        self,
        path: Optional[Path] = None,
        discovery_ttl_days: int = 7,
    ) -> None:
        self.path = path or Path(".cache") / "startgg" / "smash.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.discovery_ttl = timedelta(days=discovery_ttl_days)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # --------------------------------------------------------------------- #
    # Schema & lifecycle
    # --------------------------------------------------------------------- #

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.conn.close()

    def _ensure_schema(self) -> None:
        """Create tables if they do not already exist."""
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY,
                slug TEXT,
                name TEXT,
                city TEXT,
                state TEXT,
                country TEXT,
                start_at INTEGER,
                end_at INTEGER,
                num_attendees INTEGER,
                videogame_id INTEGER,
                last_synced INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tournaments_state_game_start
              ON tournaments(state, videogame_id, start_at DESC);

            CREATE TABLE IF NOT EXISTS discoveries (
                state TEXT NOT NULL,
                videogame_id INTEGER NOT NULL,
                last_synced INTEGER NOT NULL,
                PRIMARY KEY (state, videogame_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                tournament_id INTEGER NOT NULL,
                slug TEXT,
                name TEXT,
                start_at INTEGER,
                num_entrants INTEGER,
                videogame_id INTEGER,
                payload TEXT NOT NULL,
                last_synced INTEGER NOT NULL,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_events_tournament
              ON events(tournament_id);

            CREATE TABLE IF NOT EXISTS event_payloads (
                event_id INTEGER PRIMARY KEY,
                seeds_json TEXT NOT NULL,
                standings_json TEXT NOT NULL,
                sets_json TEXT NOT NULL,
                last_synced INTEGER NOT NULL,
                FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS player_metrics (
                state TEXT NOT NULL,
                videogame_id INTEGER NOT NULL,
                months_back INTEGER NOT NULL,
                target_character TEXT NOT NULL,
                player_id INTEGER NOT NULL,
                gamer_tag TEXT,
                weighted_win_rate REAL,
                opponent_strength REAL,
                computed_at INTEGER NOT NULL,
                PRIMARY KEY (state, videogame_id, months_back, target_character, player_id)
            );

            CREATE INDEX IF NOT EXISTS idx_player_metrics_state
              ON player_metrics(state, videogame_id, months_back, target_character);
            """
        )
        self.conn.commit()
        # Ensure new columns exist for older databases.
        self._ensure_column("tournaments", "country", "TEXT")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        """Add a column to an existing table if it is missing."""
        info = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in info):
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.conn.commit()

    # --------------------------------------------------------------------- #
    # Discovery metadata
    # --------------------------------------------------------------------- #

    def discovery_is_stale(self, state: str, videogame_id: int) -> bool:
        """Return True when the tournament listing needs a refresh."""
        row = self.conn.execute(
            "SELECT last_synced FROM discoveries WHERE state = ? AND videogame_id = ?",
            (state.upper(), int(videogame_id)),
        ).fetchone()
        if row is None:
            return True
        last_synced = datetime.fromtimestamp(row["last_synced"], tz=timezone.utc)
        return (datetime.now(timezone.utc) - last_synced) >= self.discovery_ttl

    def record_discovery(self, state: str, videogame_id: int) -> None:
        """Persist the timestamp for the most recent tournament discovery run."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO discoveries(state, videogame_id, last_synced)
                VALUES (?, ?, ?)
                ON CONFLICT(state, videogame_id) DO UPDATE SET last_synced = excluded.last_synced
                """,
                (state.upper(), int(videogame_id), now_ts),
            )

    # --------------------------------------------------------------------- #
    # Tournaments
    # --------------------------------------------------------------------- #

    def upsert_tournaments(self, tournaments: Iterable[Dict], videogame_id: int) -> None:
        """Insert or update tournament rows after hitting the API."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        with self.conn:
            for tourney in tournaments:
                self.conn.execute(
                    """
                    INSERT INTO tournaments(
                        id, slug, name, city, state, country, start_at, end_at,
                        num_attendees, videogame_id, last_synced
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        slug = excluded.slug,
                        name = excluded.name,
                        city = excluded.city,
                        state = excluded.state,
                        country = excluded.country,
                        start_at = excluded.start_at,
                        end_at = excluded.end_at,
                        num_attendees = excluded.num_attendees,
                        videogame_id = excluded.videogame_id,
                        last_synced = excluded.last_synced
                    """,
                    (
                        int(tourney.get("id")),
                        tourney.get("slug"),
                        tourney.get("name"),
                        tourney.get("city"),
                        (tourney.get("addrState") or tourney.get("state", "")),
                        tourney.get("addrCountry"),
                        tourney.get("startAt"),
                        tourney.get("endAt"),
                        tourney.get("numAttendees"),
                        int(videogame_id),
                        now_ts,
                    ),
                )

    def load_tournaments(
        self,
        state: str,
        videogame_id: int,
        cutoff_ts: int,
    ) -> List[Dict]:
        """Return tournaments in the requested window from SQLite."""
        rows = self.conn.execute(
            """
            SELECT *
              FROM tournaments
             WHERE state = ?
               AND videogame_id = ?
               AND start_at >= ?
             ORDER BY start_at DESC
            """,
            (state.upper(), int(videogame_id), cutoff_ts),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "slug": row["slug"],
                "name": row["name"],
                "city": row["city"],
                "addrState": row["state"],
                "addrCountry": row["country"],
                "startAt": row["start_at"],
                "endAt": row["end_at"],
                "numAttendees": row["num_attendees"],
            }
            for row in rows
        ]

    # --------------------------------------------------------------------- #
    # Events
    # --------------------------------------------------------------------- #

    def save_events(self, tournament_id: int, events: Iterable[Dict]) -> None:
        """Persist event metadata for a tournament."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        with self.conn:
            for event in events:
                payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
                videogame = event.get("videogame") or {}
                videogame_id = videogame.get("id")
                self.conn.execute(
                    """
                    INSERT INTO events(
                        id, tournament_id, slug, name, start_at, num_entrants,
                        videogame_id, payload, last_synced
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        slug = excluded.slug,
                        name = excluded.name,
                        start_at = excluded.start_at,
                        num_entrants = excluded.num_entrants,
                        videogame_id = excluded.videogame_id,
                        payload = excluded.payload,
                        last_synced = excluded.last_synced
                    """,
                    (
                        int(event.get("id")),
                        int(tournament_id),
                        event.get("slug"),
                        event.get("name"),
                        event.get("startAt"),
                        event.get("numEntrants"),
                        int(videogame_id) if videogame_id is not None else None,
                        payload,
                        now_ts,
                    ),
                )

    def load_events(self, tournament_id: int) -> List[Dict]:
        """Load persisted events for a tournament."""
        rows = self.conn.execute(
            """
            SELECT payload
              FROM events
             WHERE tournament_id = ?
             ORDER BY start_at DESC
            """,
            (int(tournament_id),),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    # --------------------------------------------------------------------- #
    # Event bundles (seeds/standings/sets)
    # --------------------------------------------------------------------- #

    def save_event_bundle(
        self,
        event_id: int,
        seeds: Iterable[Dict],
        standings: Iterable[Dict],
        sets: Iterable[Dict],
    ) -> None:
        """Persist the per-event bundle once downloaded."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO event_payloads(
                    event_id, seeds_json, standings_json, sets_json, last_synced
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    seeds_json = excluded.seeds_json,
                    standings_json = excluded.standings_json,
                    sets_json = excluded.sets_json,
                    last_synced = excluded.last_synced
                """,
                (
                    int(event_id),
                    json.dumps(list(seeds), separators=(",", ":"), ensure_ascii=False),
                    json.dumps(list(standings), separators=(",", ":"), ensure_ascii=False),
                    json.dumps(list(sets), separators=(",", ":"), ensure_ascii=False),
                    now_ts,
                ),
            )

    def load_event_bundle(self, event_id: int) -> Optional[Dict]:
        """Return a cached bundle for the event, if available."""
        row = self.conn.execute(
            """
            SELECT seeds_json, standings_json, sets_json
              FROM event_payloads
             WHERE event_id = ?
            """,
            (int(event_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "seeds": json.loads(row["seeds_json"]),
            "standings": json.loads(row["standings_json"]),
            "sets": json.loads(row["sets_json"]),
        }

    # --------------------------------------------------------------------- #
    # Precomputed player metrics
    # --------------------------------------------------------------------- #

    def list_states_with_data(self, videogame_id: int) -> List[str]:
        """Return sorted list of distinct tournament states for the given game."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT state
              FROM tournaments
             WHERE videogame_id = ?
               AND state IS NOT NULL
               AND TRIM(state) != ''
             ORDER BY state ASC
            """,
            (int(videogame_id),),
        ).fetchall()
        return [row["state"].upper() for row in rows]

    def replace_player_metrics(
        self,
        *,
        state: str,
        videogame_id: int,
        months_back: int,
        target_character: str,
        rows: Iterable[Dict],
    ) -> None:
        """Replace the stored player metrics for a state with the provided rows."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        normalized_state = state.upper()
        normalized_character = target_character or "Marth"
        with self.conn:
            self.conn.execute(
                """
                DELETE FROM player_metrics
                      WHERE state = ?
                        AND videogame_id = ?
                        AND months_back = ?
                        AND target_character = ?
                """,
                (
                    normalized_state,
                    int(videogame_id),
                    int(months_back),
                    normalized_character,
                ),
            )
            if not rows:
                return
            self.conn.executemany(
                """
                INSERT INTO player_metrics(
                    state,
                    videogame_id,
                    months_back,
                    target_character,
                    player_id,
                    gamer_tag,
                    weighted_win_rate,
                    opponent_strength,
                    computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        normalized_state,
                        int(videogame_id),
                        int(months_back),
                        normalized_character,
                        int(row.get("player_id")),
                        row.get("gamer_tag"),
                        row.get("weighted_win_rate"),
                        row.get("opponent_strength"),
                        now_ts,
                    )
                    for row in rows
                ],
            )

    def load_player_metrics(
        self,
        *,
        state: str,
        videogame_id: int,
        months_back: int,
        target_character: str,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """Return persisted player metrics sorted by weighted win rate."""
        query = """
            SELECT player_id,
                   gamer_tag,
                   weighted_win_rate,
                   opponent_strength,
                   computed_at
              FROM player_metrics
             WHERE state = ?
               AND videogame_id = ?
               AND months_back = ?
               AND target_character = ?
             ORDER BY (weighted_win_rate IS NULL),
                      weighted_win_rate DESC,
                      (opponent_strength IS NULL),
                      opponent_strength DESC
        """
        params: List[Any] = [
            state.upper(),
            int(videogame_id),
            int(months_back),
            target_character,
        ]
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(int(limit))
        rows = self.conn.execute(query, params).fetchall()
        return [
            {
                "player_id": row["player_id"],
                "gamer_tag": row["gamer_tag"],
                "weighted_win_rate": row["weighted_win_rate"],
                "opponent_strength": row["opponent_strength"],
                "computed_at": row["computed_at"],
            }
            for row in rows
        ]
