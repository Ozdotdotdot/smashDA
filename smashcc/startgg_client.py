"""
startgg_client.py
-----------------

Utility client for interacting with the start.gg GraphQL API.

Responsibilities:
    * Inject authentication from the environment (`STARTGG_API_TOKEN`).
    * Provide a simple caching layer to keep repeated queries offline friendly.
    * Offer helpers for paginated tournament discovery scoped to GA Ultimate.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

import shutil
import time
import requests

STARTGG_API_URL = "https://api.start.gg/gql/alpha"


def _default_cache_dir() -> Path:
    """Return the default on-disk cache path."""
    return Path(".cache") / "startgg"


def _make_cache_key(query: str, variables: Optional[Dict]) -> str:
    """
    Generate a stable hash for a GraphQL request so the payload
    can be persisted between runs.
    """
    hasher = hashlib.sha256()
    hasher.update(query.encode("utf-8"))
    if variables:
        normalized = json.dumps(variables, sort_keys=True, separators=(",", ":"))
        hasher.update(normalized.encode("utf-8"))
    return hasher.hexdigest()


@dataclass(frozen=True)
class TournamentFilter:
    """Lightweight value object describing the tournament discovery filters."""

    state: str = "GA"
    videogame_id: int = 1386  # Ultimate by default
    months_back: int = 6
    per_page: int = 50
    window_offset: int = 0
    window_size: Optional[int] = None
    start_ts_override: Optional[int] = None
    end_ts_override: Optional[int] = None
    name_contains: Optional[tuple[str, ...]] = None
    slug_contains: Optional[tuple[str, ...]] = None

    def window_months(self) -> int:
        """Return the number of months covered by the window."""
        span = self.window_size or self.months_back
        return max(1, int(span))

    def window_bounds(self) -> tuple[int, int]:
        """Return (start_ts, end_ts) unix timestamps for the desired window."""
        offset = max(0, int(self.window_offset))
        now = datetime.now(timezone.utc)
        window_end = now - timedelta(days=30 * offset)
        window_start = window_end - timedelta(days=30 * self.window_months())
        start = (
            int(self.start_ts_override)
            if self.start_ts_override is not None
            else int(window_start.timestamp())
        )
        end = (
            int(self.end_ts_override)
            if self.end_ts_override is not None
            else int(window_end.timestamp())
        )
        return start, end


class StartGGClient:
    """Minimal start.gg client with caching + pagination helpers."""

    def __init__(
        self,
        token: Optional[str] = None,
        api_url: str = STARTGG_API_URL,
        cache_dir: Optional[Path] = None,
        use_cache: bool = True,
        stale_after_days: Optional[int] = 7,
        archive_stale: bool = True,
    ) -> None:
        self._token = token or os.getenv("STARTGG_API_TOKEN")
        if not self._token:
            raise RuntimeError(
                "STARTGG_API_TOKEN is not set; export it before running live queries."
            )

        self.api_url = api_url
        self.cache_dir = cache_dir or _default_cache_dir()
        self.use_cache = use_cache
        self.archive_stale = archive_stale
        self.stale_after = (
            timedelta(days=stale_after_days) if stale_after_days is not None else None
        )
        self.archive_dir = self.cache_dir / "archive"
        self.session = requests.Session()
        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            if self.archive_stale:
                self.archive_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL request, hitting the cache first when enabled."""
        cache_key = _make_cache_key(query, variables)
        cache_path = self.cache_dir / f"{cache_key}.json"
        cache_stale = False
        if self.use_cache and cache_path.exists():
            cache_stale = self._cache_is_stale(cache_path)
            if not cache_stale:
                with cache_path.open("r", encoding="utf-8") as fp:
                    return json.load(fp)

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload = {"query": query, "variables": variables or {}}
        attempt = 0
        max_attempts = 10

        while True:
            response = self.session.post(self.api_url, json=payload, headers=headers, timeout=30)

            if response.status_code == 429 and attempt < max_attempts:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait_seconds = float(retry_after)
                except (TypeError, ValueError):
                    wait_seconds = min(60, 2 ** attempt)
                time.sleep(wait_seconds)
                attempt += 1
                continue

            response.raise_for_status()
            data = response.json()
            break

        if "errors" in data:
            raise RuntimeError(f"GraphQL error: {data['errors']}")

        if self.use_cache:
            if cache_stale and self.archive_stale:
                self._archive_cache_file(cache_path)
            with cache_path.open("w", encoding="utf-8") as fp:
                json.dump(data["data"], fp)

        return data["data"]

    def iter_recent_tournaments(
        self, filt: TournamentFilter
    ) -> Iterator[Dict]:
        """
        Yield tournaments in the requested state/video game that fall within
        the requested window (supports offsets for backfilling older months).
        """
        window_start, window_end = filt.window_bounds()
        page = 1

        query = """
        query RecentTournaments($state: String!, $videogameId: ID!, $perPage: Int!, $page: Int!) {
          tournaments(query: {
            page: $page,
            perPage: $perPage,
            sortBy: "startAt desc",
            filter: {
              addrState: $state,
              videogameIds: [$videogameId],
              past: true
            }
          }) {
            nodes {
              id
              slug
              name
              city
              addrState
              countryCode
              startAt
              endAt
              numAttendees
            }
          }
        }
        """

        while True:
            result = self.execute(
                query,
                {
                    "state": filt.state,
                    "videogameId": filt.videogame_id,
                    "perPage": filt.per_page,
                    "page": page,
                },
            )
            nodes: Iterable[Dict] = result.get("tournaments", {}).get("nodes") or []
            if not nodes:
                break

            reached_older = False
            for node in nodes:
                if node.get("addrCountry") is None:
                    node["addrCountry"] = node.get("countryCode")
                start_at = node.get("startAt") or 0
                if start_at > window_end:
                    continue
                if start_at < window_start:
                    reached_older = True
                    break
                yield node

            if reached_older:
                break

            page += 1

    def _cache_is_stale(self, cache_path: Path) -> bool:
        """Check whether a cached payload should be refreshed."""
        if self.stale_after is None:
            return False
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
        age = datetime.now(timezone.utc) - mtime
        return age >= self.stale_after

    def _archive_cache_file(self, cache_path: Path) -> None:
        """Keep the previous payload in an archive before refreshing."""
        if not cache_path.exists():
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        archive_name = f"{cache_path.stem}-{timestamp}{cache_path.suffix}"
        target = self.archive_dir / archive_name
        shutil.copy2(cache_path, target)
