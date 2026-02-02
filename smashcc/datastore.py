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


def _bool_to_int(value: Optional[bool]) -> Optional[int]:
    """Convert optional booleans to SQLite-friendly integers."""
    if value is None:
        return None
    return 1 if bool(value) else 0


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

            CREATE TABLE IF NOT EXISTS tournament_series_matches (
                tournament_id INTEGER PRIMARY KEY,
                name_matches TEXT,
                slug_matches TEXT,
                last_synced INTEGER NOT NULL,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
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
                avg_seed_delta REAL,
                upset_rate REAL,
                activity_score REAL,
                home_state TEXT,
                home_state_inferred INTEGER,
                avg_event_entrants REAL,
                max_event_entrants REAL,
                large_event_share REAL,
                latest_event_start INTEGER,
                computed_at INTEGER NOT NULL,
                PRIMARY KEY (state, videogame_id, months_back, target_character, player_id)
            );

            CREATE INDEX IF NOT EXISTS idx_player_metrics_state
              ON player_metrics(state, videogame_id, months_back, target_character);

            CREATE TABLE IF NOT EXISTS player_series_metrics (
                state TEXT NOT NULL,
                videogame_id INTEGER NOT NULL,
                months_back INTEGER NOT NULL,
                window_offset INTEGER NOT NULL,
                window_size INTEGER NOT NULL,
                series_key TEXT NOT NULL,
                series_name_term TEXT,
                series_slug_term TEXT,
                player_id INTEGER NOT NULL,
                gamer_tag TEXT,
                weighted_win_rate REAL,
                opponent_strength REAL,
                avg_seed_delta REAL,
                upset_rate REAL,
                activity_score REAL,
                home_state TEXT,
                home_state_inferred INTEGER,
                avg_event_entrants REAL,
                max_event_entrants REAL,
                large_event_share REAL,
                latest_event_start INTEGER,
                computed_at INTEGER NOT NULL,
                PRIMARY KEY (
                    state,
                    videogame_id,
                    months_back,
                    window_offset,
                    window_size,
                    series_key,
                    player_id
                )
            );

            CREATE INDEX IF NOT EXISTS idx_player_series_metrics_state
              ON player_series_metrics(state, videogame_id, months_back, window_offset, window_size, series_key);
            """
        )
        self.conn.commit()
        # Ensure new columns exist for older databases.
        self._ensure_column("tournaments", "country", "TEXT")
        self._ensure_column("player_metrics", "home_state", "TEXT")
        self._ensure_column("player_metrics", "home_state_inferred", "INTEGER")
        self._ensure_column("player_metrics", "avg_event_entrants", "REAL")
        self._ensure_column("player_metrics", "max_event_entrants", "REAL")
        self._ensure_column("player_metrics", "large_event_share", "REAL")
        self._ensure_column("player_metrics", "latest_event_start", "INTEGER")
        self._ensure_column("player_metrics", "avg_seed_delta", "REAL")
        self._ensure_column("player_metrics", "upset_rate", "REAL")
        self._ensure_column("player_metrics", "activity_score", "REAL")
        self._ensure_column("player_series_metrics", "avg_seed_delta", "REAL")
        self._ensure_column("player_series_metrics", "upset_rate", "REAL")
        self._ensure_column("player_series_metrics", "activity_score", "REAL")
        self._ensure_column("tournaments", "events_checked", "INTEGER")

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
        start_ts: int,
        end_ts: Optional[int] = None,
    ) -> List[Dict]:
        """Return tournaments in the requested window from SQLite."""
        query = """
            SELECT *
              FROM tournaments
             WHERE state = ?
               AND videogame_id = ?
               AND start_at >= ?
        """
        params: List[Any] = [state.upper(), int(videogame_id), int(start_ts)]
        if end_ts is not None:
            query += " AND start_at <= ?"
            params.append(int(end_ts))
        query += " ORDER BY start_at DESC"
        rows = self.conn.execute(query, params).fetchall()
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

    def save_tournament_series_matches(
        self,
        records: Iterable[tuple[int, Iterable[str], Iterable[str]]],
    ) -> None:
        """Persist per-tournament series/substring matches for later querying."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        payloads = []
        for tournament_id, name_matches, slug_matches in records:
            payloads.append(
                (
                    int(tournament_id),
                    json.dumps(list(name_matches), separators=(",", ":"), ensure_ascii=False),
                    json.dumps(list(slug_matches), separators=(",", ":"), ensure_ascii=False),
                    now_ts,
                )
            )

        if not payloads:
            return

        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO tournament_series_matches(
                    tournament_id, name_matches, slug_matches, last_synced
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tournament_id) DO UPDATE SET
                    name_matches = excluded.name_matches,
                    slug_matches = excluded.slug_matches,
                    last_synced = excluded.last_synced
                """,
                payloads,
            )

    def load_tournament_series_matches(
        self, tournament_ids: Iterable[int]
    ) -> Dict[int, Dict[str, List[str]]]:
        """Return stored series match metadata for the requested tournaments."""
        ids = [int(tid) for tid in tournament_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"""
            SELECT tournament_id, name_matches, slug_matches
              FROM tournament_series_matches
             WHERE tournament_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        result: Dict[int, Dict[str, List[str]]] = {}
        for row in rows:
            result[int(row["tournament_id"])] = {
                "name_matches": json.loads(row["name_matches"] or "[]"),
                "slug_matches": json.loads(row["slug_matches"] or "[]"),
            }
        return result

    # --------------------------------------------------------------------- #
    # Events
    # --------------------------------------------------------------------- #

    def save_events(self, tournament_id: int, events: Iterable[Dict]) -> None:
        """Persist event metadata for a tournament."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        events = list(events)
        with self.conn:
            if not events:
                self.conn.execute(
                    """
                    UPDATE tournaments
                       SET events_checked = 1,
                           last_synced = ?
                     WHERE id = ?
                    """,
                    (now_ts, int(tournament_id)),
                )
                return
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
            self.conn.execute(
                """
                UPDATE tournaments
                   SET events_checked = 1,
                       last_synced = ?
                 WHERE id = ?
                """,
                (now_ts, int(tournament_id)),
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
        if rows:
            return [json.loads(row["payload"]) for row in rows]
        marker = self.conn.execute(
            """
            SELECT events_checked
              FROM tournaments
             WHERE id = ?
            """,
            (int(tournament_id),),
        ).fetchone()
        if marker and marker["events_checked"]:
            return []
        return None

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
                    avg_seed_delta,
                    upset_rate,
                    activity_score,
                    home_state,
                    home_state_inferred,
                    avg_event_entrants,
                    max_event_entrants,
                    large_event_share,
                    latest_event_start,
                    computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        row.get("avg_seed_delta"),
                        row.get("upset_rate"),
                        row.get("activity_score"),
                        row.get("home_state"),
                        _bool_to_int(row.get("home_state_inferred")),
                        row.get("avg_event_entrants"),
                        row.get("max_event_entrants"),
                        row.get("large_event_share"),
                        row.get("latest_event_start"),
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
                   avg_seed_delta,
                   upset_rate,
                   activity_score,
                   home_state,
                   home_state_inferred,
                   avg_event_entrants,
                   max_event_entrants,
                   large_event_share,
                   latest_event_start,
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
                "avg_seed_delta": row["avg_seed_delta"],
                "upset_rate": row["upset_rate"],
                "activity_score": row["activity_score"],
                "home_state": row["home_state"],
                "home_state_inferred": bool(row["home_state_inferred"])
                if row["home_state_inferred"] is not None
                else None,
                "avg_event_entrants": row["avg_event_entrants"],
                "max_event_entrants": row["max_event_entrants"],
                "large_event_share": row["large_event_share"],
                "latest_event_start": row["latest_event_start"],
                "computed_at": row["computed_at"],
            }
            for row in rows
        ]

    # --------------------------------------------------------------------- #
    # Series metrics
    # --------------------------------------------------------------------- #

    def replace_series_metrics(
        self,
        *,
        state: str,
        videogame_id: int,
        months_back: int,
        window_offset: int,
        window_size: Optional[int],
        series_key: str,
        series_name_term: Optional[str],
        series_slug_term: Optional[str],
        rows: Iterable[Dict],
    ) -> None:
        """Replace persisted series-scoped metrics for a given series key."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        normalized_state = state.upper()
        normalized_series_key = series_key or "unknown"
        normalized_window_size = window_size if window_size is not None else -1

        with self.conn:
            self.conn.execute(
                """
                DELETE FROM player_series_metrics
                      WHERE state = ?
                        AND videogame_id = ?
                        AND months_back = ?
                        AND window_offset = ?
                        AND window_size = ?
                        AND series_key = ?
                """,
                (
                    normalized_state,
                    int(videogame_id),
                    int(months_back),
                    int(window_offset),
                    int(normalized_window_size),
                    normalized_series_key,
                ),
            )
            if not rows:
                return
            self.conn.executemany(
                """
                INSERT INTO player_series_metrics(
                    state,
                    videogame_id,
                    months_back,
                    window_offset,
                    window_size,
                    series_key,
                    series_name_term,
                    series_slug_term,
                    player_id,
                    gamer_tag,
                    weighted_win_rate,
                    opponent_strength,
                    avg_seed_delta,
                    upset_rate,
                    activity_score,
                    home_state,
                    home_state_inferred,
                    avg_event_entrants,
                    max_event_entrants,
                    large_event_share,
                    latest_event_start,
                    computed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        normalized_state,
                        int(videogame_id),
                        int(months_back),
                        int(window_offset),
                        int(normalized_window_size),
                        normalized_series_key,
                        series_name_term,
                        series_slug_term,
                        int(row.get("player_id")),
                        row.get("gamer_tag"),
                        row.get("weighted_win_rate"),
                        row.get("opponent_strength"),
                        row.get("avg_seed_delta"),
                        row.get("upset_rate"),
                        row.get("activity_score"),
                        row.get("home_state"),
                        _bool_to_int(row.get("home_state_inferred")),
                        row.get("avg_event_entrants"),
                        row.get("max_event_entrants"),
                        row.get("large_event_share"),
                        row.get("latest_event_start"),
                        now_ts,
                    )
                    for row in rows
                ],
            )

    def load_series_metrics(
        self,
        *,
        state: str,
        videogame_id: int,
        months_back: int,
        window_offset: int,
        window_size: Optional[int],
        series_key: str,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """Return persisted metrics for a specific series key."""
        normalized_state = state.upper()
        normalized_series_key = series_key or "unknown"
        normalized_window_size = window_size if window_size is not None else -1
        query = """
            SELECT player_id,
                   gamer_tag,
                   weighted_win_rate,
                   opponent_strength,
                   avg_seed_delta,
                   upset_rate,
                   activity_score,
                   home_state,
                   home_state_inferred,
                   avg_event_entrants,
                   max_event_entrants,
                   large_event_share,
                   latest_event_start,
                   computed_at
              FROM player_series_metrics
             WHERE state = ?
               AND videogame_id = ?
               AND months_back = ?
               AND window_offset = ?
               AND window_size = ?
               AND series_key = ?
             ORDER BY (weighted_win_rate IS NULL),
                      weighted_win_rate DESC,
                      (opponent_strength IS NULL),
                      opponent_strength DESC
        """
        params: List[Any] = [
            normalized_state,
            int(videogame_id),
            int(months_back),
            int(window_offset),
            int(normalized_window_size),
            normalized_series_key,
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
                "avg_seed_delta": row["avg_seed_delta"],
                "upset_rate": row["upset_rate"],
                "activity_score": row["activity_score"],
                "home_state": row["home_state"],
                "home_state_inferred": bool(row["home_state_inferred"])
                if row["home_state_inferred"] is not None
                else None,
                "avg_event_entrants": row["avg_event_entrants"],
                "max_event_entrants": row["max_event_entrants"],
                "large_event_share": row["large_event_share"],
                "latest_event_start": row["latest_event_start"],
                "computed_at": row["computed_at"],
            }
            for row in rows
        ]

    def find_series_keys(
        self,
        *,
        state: str,
        videogame_id: int,
        months_back: int,
        window_offset: int,
        window_size: Optional[int],
        name_contains: Optional[str] = None,
        slug_contains: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """Return series keys matching optional name/slug substrings."""
        normalized_state = state.upper()
        normalized_window_size = window_size if window_size is not None else -1
        clauses = [
            "state = ?",
            "videogame_id = ?",
            "months_back = ?",
            "window_offset = ?",
            "window_size = ?",
        ]
        params: List[Any] = [
            normalized_state,
            int(videogame_id),
            int(months_back),
            int(window_offset),
            int(normalized_window_size),
        ]

        if name_contains:
            clauses.append("LOWER(series_name_term) LIKE ?")
            params.append(f"%{name_contains.lower()}%")
        if slug_contains:
            clauses.append("LOWER(series_slug_term) LIKE ?")
            params.append(f"%{slug_contains.lower()}%")

        where = " AND ".join(clauses)
        sql = f"""
            SELECT series_key,
                   series_name_term,
                   series_slug_term,
                   COUNT(*) AS player_rows
              FROM player_series_metrics
             WHERE {where}
             GROUP BY series_key, series_name_term, series_slug_term
             ORDER BY player_rows DESC, series_key
        """
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))

        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "series_key": row["series_key"],
                "series_name_term": row["series_name_term"],
                "series_slug_term": row["series_slug_term"],
                "player_rows": row["player_rows"],
            }
            for row in rows
        ]
