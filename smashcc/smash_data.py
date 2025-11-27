"""
smash_data.py
-------------

Higher-level data access helpers built on top of :mod:`startgg_client`.
These functions handle the repetitive pagination details required to join
seed, standing, and set information for tournaments in a six-month window.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time
from typing import Dict, Iterable, List, Optional

from .datastore import SQLiteStore
from .startgg_client import StartGGClient, TournamentFilter


EVENT_FIELDS = """
    id
    name
    slug
    startAt
    numEntrants
    teamRosterSize {
      minPlayers
      maxPlayers
    }
    entrantSizeMin
    entrantSizeMax
    phases {
      id
      name
      phaseOrder
    }
    videogame {
      id
      name
    }
"""

PARTICIPANT_FIELDS = """
    id
    gamerTag
    prefix
    user {
      location {
        city
        state
        country
      }
    }
    player {
      id
      gamerTag
      prefix
      user {
        location {
          city
          state
          country
        }
      }
    }
"""


def fetch_recent_tournaments(
    client: StartGGClient,
    filt: Optional[TournamentFilter] = None,
    store: Optional[SQLiteStore] = None,
    suppress_logs: bool = False,
    offline_only: bool = False,
) -> List[Dict]:
    """Return tournaments in scope for downstream processing."""
    filt = filt or TournamentFilter()
    name_terms = tuple(t.strip().lower() for t in (filt.name_contains or ()) if t and t.strip())
    slug_terms = tuple(t.strip().lower() for t in (filt.slug_contains or ()) if t and t.strip())
    tournaments: List[Dict] = []
    window_start_ts, window_end_ts = filt.window_bounds()
    if store is not None:
        if not suppress_logs:
            print("Checking local database for cached tournaments...")
        tournaments = store.load_tournaments(
            filt.state,
            filt.videogame_id,
            window_start_ts,
            window_end_ts,
        )
        coverage_missing = False
        discovery_stale = store.discovery_is_stale(filt.state, filt.videogame_id)
        if tournaments and not suppress_logs:
            print(f"Found {len(tournaments)} tournament(s) in local database.")
        elif not tournaments and not suppress_logs:
            print("No cached tournaments found in local database.")
        if tournaments:
            start_at_values = [t.get("startAt") or 0 for t in tournaments]
            earliest = min(start_at_values)
            latest = max(start_at_values)
            coverage_missing = earliest > window_start_ts or latest < window_end_ts
        if offline_only:
            if not suppress_logs:
                print("Offline-only mode: using cached tournaments without refreshing from start.gg.")
            if coverage_missing and not suppress_logs:
                print("Warning: cached tournaments do not fully cover the requested window.")
            if discovery_stale and not suppress_logs:
                print("Warning: cached tournament discovery is stale.")
        if (not tournaments or discovery_stale or coverage_missing) and not offline_only:
            delta_query = False
            delta_filter = filt
            if discovery_stale and tournaments and not coverage_missing:
                latest_start = max(t.get("startAt") or 0 for t in tournaments)
                delta_start_ts = max(window_start_ts, latest_start)
                delta_filter = TournamentFilter(
                    state=filt.state,
                    videogame_id=filt.videogame_id,
                    months_back=filt.months_back,
                    per_page=filt.per_page,
                    window_offset=filt.window_offset,
                    window_size=filt.window_size,
                    start_ts_override=delta_start_ts,
                    end_ts_override=window_end_ts,
                )
                cutoff_date = datetime.fromtimestamp(delta_start_ts, tz=timezone.utc).strftime(
                    "%Y-%m-%d"
                )
                if not suppress_logs:
                    print(
                        f"Local data is stale; querying start.gg for tournaments after {cutoff_date} (delta only)..."
                    )
                delta_query = True
            else:
                if not suppress_logs:
                    print("Local data missing or coverage incomplete; querying start.gg for recent tournaments...")

            fresh = list(client.iter_recent_tournaments(delta_filter))
            if fresh:
                store.upsert_tournaments(fresh, filt.videogame_id)
            store.record_discovery(filt.state, filt.videogame_id)

            if delta_query:
                merged = {str(t.get("id")): t for t in tournaments}
                for tourney in fresh:
                    merged[str(tourney.get("id"))] = tourney
                tournaments = sorted(
                    merged.values(),
                    key=lambda t: t.get("startAt") or 0,
                    reverse=True,
                )
            else:
                tournaments = fresh
        else:
            print("Using cached tournaments from local database.")
    else:
        if offline_only:
            raise RuntimeError(
                "Offline-only mode requires a local SQLite store; provide --store-path or enable caching."
            )
        if not suppress_logs:
            print("No local database configured; querying start.gg for recent tournaments...")
        tournaments = list(client.iter_recent_tournaments(filt))
    # Filter again in case the DB contains tournaments outside the requested window.
    filtered: List[Dict] = []
    matched_records = []

    def _matching_terms(value: str, terms: tuple[str, ...]) -> List[str]:
        val = (value or "").lower()
        return [term for term in terms if term in val]

    for tournament in tournaments:
        start_at = tournament.get("startAt") or 0
        if not (window_start_ts <= start_at <= window_end_ts):
            continue

        name_matches = _matching_terms(tournament.get("name") or "", name_terms)
        slug_matches = _matching_terms(tournament.get("slug") or "", slug_terms)
        if name_terms or slug_terms:
            if not name_matches and not slug_matches:
                continue

        tournament["_name_matches"] = name_matches
        tournament["_slug_matches"] = slug_matches
        filtered.append(tournament)
        if store is not None and (name_matches or slug_matches):
            tourney_id = tournament.get("id")
            if tourney_id is not None:
                matched_records.append(
                    (
                        int(tourney_id),
                        name_matches,
                        slug_matches,
                    )
                )

    if store is not None and matched_records:
        store.save_tournament_series_matches(matched_records)

    return filtered


def fetch_tournament_events(
    client: StartGGClient,
    tournament_id: int,
    store: Optional[SQLiteStore] = None,
    offline_only: bool = False,
) -> List[Dict]:
    """Fetch events for a tournament, including roster sizing metadata."""
    if store is not None:
        cached_events = store.load_events(tournament_id)
        if cached_events is not None:
            return cached_events
    if offline_only:
        print(
            f"Warning: offline-only enabled but events for tournament {tournament_id} are not cached; skipping."
        )
        return []

    query = f"""
    query TournamentEvents($tournamentId: ID!) {{
      tournament(id: $tournamentId) {{
        id
        slug
        name
        city
        addrState
        countryCode
        startAt
        events {{
          {EVENT_FIELDS}
        }}
      }}
    }}
    """
    data = client.execute(query, {"tournamentId": tournament_id})
    tournament = data.get("tournament") or {}
    if tournament and tournament.get("addrCountry") is None:
        tournament["addrCountry"] = tournament.get("countryCode")
    events = tournament.get("events") or []
    for event in events:
        event["_tournament"] = {
            "id": tournament.get("id"),
            "slug": tournament.get("slug"),
            "name": tournament.get("name"),
            "city": tournament.get("city"),
            "addrState": tournament.get("addrState"),
            "addrCountry": tournament.get("addrCountry"),
            "startAt": tournament.get("startAt"),
    }
    if store is not None:
        store.save_events(tournament_id, events)
    return events


def _paginate_event_field(
    client: StartGGClient,
    event_id: int,
    field_fragment: str,
    per_page: int = 50,
) -> List[Dict]:
    """
    Generic helper to page through an event subresource (seeds, standings, sets).
    `field_fragment` is a GraphQL snippet returning the desired field, including
    the `pageInfo` and `nodes` structure.
    """
    query = f"""
    query EventData($eventId: ID!, $page: Int!, $perPage: Int!) {{
      event(id: $eventId) {{
        {field_fragment}
      }}
    }}
    """
    min_per_page = 5
    current_per_page = max(min_per_page, per_page)

    while True:
        results: List[Dict] = []
        page = 1
        total_pages: Optional[int] = None
        try:
            while True:
                payload = client.execute(
                    query,
                    {"eventId": event_id, "page": page, "perPage": current_per_page},
                )
                event = payload.get("event") or {}
                if not event:
                    break

                field_name = next(iter(event.keys()))
                container = event[field_name] or {}
                nodes: Iterable[Dict] = container.get("nodes") or []
                results.extend(nodes)

                page_info = container.get("pageInfo") or {}
                total_pages = total_pages or page_info.get("totalPages") or 1

                if page >= total_pages:
                    break

                page += 1
        except RuntimeError as exc:
            message = str(exc).lower()
            if "query complexity is too high" in message and current_per_page > min_per_page:
                current_per_page = max(min_per_page, current_per_page // 2)
                time.sleep(1)
                continue
            raise
        return results


def fetch_event_seeds(client: StartGGClient, event_id: int, per_page: int = 50) -> List[Dict]:
    """Return all seeds for an event with entrant + participant metadata."""
    field_fragment = f"""
    seeds(query: {{ page: $page, perPage: $perPage }}) {{
      pageInfo {{ totalPages total }}
      nodes {{
        id
        seedNum
        entrant {{
          id
          name
          participants {{
            {PARTICIPANT_FIELDS}
          }}
        }}
      }}
    }}
    """
    return _paginate_event_field(client, event_id, field_fragment, per_page=per_page)


def fetch_event_standings(client: StartGGClient, event_id: int, per_page: int = 50) -> List[Dict]:
    """Return final standings for an event."""
    field_fragment = """
    standings(query: { page: $page, perPage: $perPage }) {
      pageInfo { totalPages total }
      nodes {
        id
        placement
        entrant {
          id
          name
        }
      }
    }
    """
    return _paginate_event_field(
        client,
        event_id,
        field_fragment,
        per_page=per_page,
    )


def fetch_event_sets(client: StartGGClient, event_id: int, per_page: int = 15) -> List[Dict]:
    """Return all sets for an event, including per-game character data."""
    field_fragment = f"""
    sets(
      page: $page,
      perPage: $perPage,
      sortType: STANDARD
    ) {{
      pageInfo {{ totalPages total }}
      nodes {{
        id
        state
        round
        fullRoundText
        completedAt
        winnerId
        slots {{
          id
          entrant {{
            id
            name
            participants {{
              {PARTICIPANT_FIELDS}
            }}
          }}
          standing {{
            placement
            stats {{
              score {{
                value
              }}
            }}
          }}
        }}
        games {{
          id
          orderNum
          winnerId
          entrant1Score
          entrant2Score
          stage {{ id name }}
          selections {{
            id
            selectionType
            selectionValue
            entrant {{ id }}
            character {{ id name }}
          }}
        }}
      }}
    }}
    """
    return _paginate_event_field(
        client,
        event_id,
        field_fragment,
        per_page=per_page,
    )


def fetch_phase_seeds(client: StartGGClient, phase_id: int, per_page: int = 50) -> List[Dict]:
    """Return seeds for a specific phase."""
    query = """
    query PhaseSeeds($phaseId: ID!, $page: Int!, $perPage: Int!) {
      phase(id: $phaseId) {
        seeds(query: { page: $page, perPage: $perPage }) {
          pageInfo { totalPages total }
          nodes {
            id
            seedNum
            entrant {
              id
              name
              participants {
                %s
              }
            }
          }
        }
      }
    }
    """ % PARTICIPANT_FIELDS

    results: List[Dict] = []
    page = 1
    total_pages: Optional[int] = None

    while True:
        payload = client.execute(
            query,
            {"phaseId": phase_id, "page": page, "perPage": per_page},
        )
        phase = payload.get("phase") or {}
        seeds_container = phase.get("seeds") or {}
        nodes: Iterable[Dict] = seeds_container.get("nodes") or []
        results.extend(nodes)

        page_info = seeds_container.get("pageInfo") or {}
        total_pages = total_pages or page_info.get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1

    return results


@dataclass
class EventBundle:
    """Convenience container bundling event-level payloads together."""

    event: Dict
    seeds: List[Dict]
    standings: List[Dict]
    sets: List[Dict]


def collect_event_bundle(
    client: StartGGClient,
    event: Dict,
    store: Optional[SQLiteStore] = None,
    offline_only: bool = False,
) -> EventBundle:
    """Gather seeds, standings, and sets for the provided event."""
    event_id = int(event["id"])
    if store is not None:
        cached = store.load_event_bundle(event_id)
        if cached is not None:
            return EventBundle(
                event=event,
                seeds=cached["seeds"],
                standings=cached["standings"],
                sets=cached["sets"],
            )
    if offline_only:
        print(
            f"Warning: offline-only enabled but event bundle for event {event_id} is not cached; skipping."
        )
        return EventBundle(event=event, seeds=[], standings=[], sets=[])
    seeds: List[Dict] = []
    # Combine seeds across phases (usually there's at least a pools & finals phase)
    for phase in event.get("phases", []) or []:
        phase_id = phase.get("id")
        if not phase_id:
            continue
        phase_seeds = fetch_phase_seeds(client, int(phase_id))
        seeds.extend(phase_seeds)
    standings = fetch_event_standings(client, event_id)
    sets = fetch_event_sets(client, event_id)
    if store is not None:
        store.save_event_bundle(event_id, seeds, standings, sets)
    return EventBundle(event=event, seeds=seeds, standings=standings, sets=sets)


@dataclass
class SetRecord:
    """Representation of a single set from a player's perspective."""

    set_id: str
    won: Optional[bool]
    opponent_entrant_id: Optional[str]
    opponent_player_ids: List[int]
    opponent_gamer_tags: List[str]
    opponent_seed: Optional[int]
    opponent_placement: Optional[int]
    round_text: Optional[str]
    completed_at: Optional[int]
    characters: List[str]


@dataclass
class PlayerEventResult:
    """Aggregated payload for a player's performance in a single event."""

    player_id: int
    gamer_tag: str
    entrant_id: str
    seed_num: Optional[int]
    placement: Optional[int]
    participant: Dict
    event: Dict
    tournament: Dict
    sets: List[SetRecord]

    @property
    def location(self) -> Dict:
        """Best-effort user location (falls back to participant-level fields)."""
        player = self.participant.get("player") or {}
        user = player.get("user") or {}
        location = user.get("location")
        if location:
            return location
        participant_user = self.participant.get("user") or {}
        return participant_user.get("location") or {}


def _extract_characters_for_entrant(set_node: Dict, entrant_id: str) -> List[str]:
    """Return the list of character names selected by the entrant within the set."""
    characters: List[str] = []
    games = set_node.get("games") or []
    for game in games:
        for selection in game.get("selections") or []:
            sel_type = (selection.get("selectionType") or "").upper()
            if sel_type and sel_type != "CHARACTER":
                continue
            entrant = selection.get("entrant") or {}
            if str(entrant.get("id")) != entrant_id:
                continue
            character = selection.get("character") or {}
            name = character.get("name")
            if name:
                characters.append(name)
    return characters


def build_player_event_results(bundle: EventBundle) -> List[PlayerEventResult]:
    """
    Join seeds, standings, and sets into per-player event records.

    This returns entries keyed by player id; doubles/teams entrants are ignored.
    """
    seeds_map: Dict[str, Optional[int]] = {}
    standings_map: Dict[str, Optional[int]] = {}
    entrant_participant_map: Dict[str, Dict] = {}
    player_info_by_entrant: Dict[str, Dict] = {}

    for seed in bundle.seeds:
        entrant = seed.get("entrant") or {}
        entrant_id = str(entrant.get("id"))
        seeds_map[entrant_id] = seed.get("seedNum")
        participants = entrant.get("participants") or []
        if len(participants) == 1:
            participant = participants[0] or {}
            player = participant.get("player") or {}
            player_id = player.get("id")
            if player_id:
                player_info_by_entrant[entrant_id] = {
                    "player": player,
                    "participant": participant,
                    "entrant": entrant,
                }
                entrant_participant_map[entrant_id] = participant

    for standing in bundle.standings:
        entrant = standing.get("entrant") or {}
        entrant_id = str(entrant.get("id"))
        standings_map[entrant_id] = standing.get("placement")
        if entrant_id not in entrant_participant_map:
            participants = entrant.get("participants") or []
            if len(participants) == 1:
                participant = participants[0] or {}
                player = participant.get("player") or {}
                player_id = player.get("id")
                if player_id:
                    player_info_by_entrant[entrant_id] = {
                        "player": player,
                        "participant": participant,
                        "entrant": entrant,
                    }
                    entrant_participant_map[entrant_id] = participant

    sets_by_player: Dict[int, List[SetRecord]] = defaultdict(list)

    for set_node in bundle.sets:
        slots = set_node.get("slots") or []
        if len(slots) < 2:
            continue

        slot_details = []
        for slot in slots:
            entrant = slot.get("entrant") or {}
            entrant_id = str(entrant.get("id"))
            participants = entrant.get("participants") or []
            player_ids = []
            gamer_tags = []
            for participant in participants:
                player = participant.get("player") or {}
                player_id = player.get("id")
                if player_id:
                    player_ids.append(int(player_id))
                    gamer_tags.append(player.get("gamerTag") or participant.get("gamerTag"))
                    if entrant_id not in player_info_by_entrant:
                        player_info_by_entrant[entrant_id] = {
                            "player": player,
                            "participant": participant,
                            "entrant": entrant,
                        }
                        entrant_participant_map[entrant_id] = participant
            slot_details.append(
                {
                    "entrant_id": entrant_id,
                    "player_ids": player_ids,
                    "gamer_tags": gamer_tags,
                }
            )

        for slot in slot_details:
            if len(slot["player_ids"]) != 1:
                continue
            entrant_id = slot["entrant_id"]
            player_id = slot["player_ids"][0]

            opponent = next(
                (s for s in slot_details if s is not slot and s["player_ids"]),
                None,
            )
            if opponent is None:
                continue
            opponent_entrant_id = opponent["entrant_id"]
            opponent_seed = seeds_map.get(opponent_entrant_id)
            opponent_placement = standings_map.get(opponent_entrant_id)

            characters = _extract_characters_for_entrant(set_node, entrant_id)
            won: Optional[bool] = None
            winner_id = set_node.get("winnerId")
            if winner_id is not None:
                won = str(winner_id) == entrant_id

            set_record = SetRecord(
                set_id=str(set_node.get("id")),
                won=won,
                opponent_entrant_id=opponent_entrant_id,
                opponent_player_ids=[int(pid) for pid in opponent["player_ids"]],
                opponent_gamer_tags=[tag for tag in opponent["gamer_tags"] if tag],
                opponent_seed=opponent_seed,
                opponent_placement=opponent_placement,
                round_text=set_node.get("fullRoundText") or set_node.get("round"),
                completed_at=set_node.get("completedAt"),
                characters=characters,
            )
            sets_by_player[player_id].append(set_record)

    results: List[PlayerEventResult] = []
    tournament_meta = bundle.event.get("_tournament") or {}
    for entrant_id, info in player_info_by_entrant.items():
        player = info.get("player") or {}
        participant = info.get("participant") or {}
        player_id = player.get("id")
        if player_id is None:
            continue
        player_id = int(player_id)
        if len(sets_by_player[player_id]) == 0 and entrant_id not in seeds_map:
            continue
        gamer_tag = player.get("gamerTag") or participant.get("gamerTag") or "Unknown"

        result = PlayerEventResult(
            player_id=player_id,
            gamer_tag=gamer_tag,
            entrant_id=entrant_id,
            seed_num=seeds_map.get(entrant_id),
            placement=standings_map.get(entrant_id),
            participant=participant,
            event=bundle.event,
            tournament=tournament_meta,
            sets=sets_by_player.get(player_id, []),
        )
        results.append(result)

    return results


def is_singles_event(event: Dict) -> bool:
    """Determine whether an event is singles based on roster size metadata."""
    entrant_min = event.get("entrantSizeMin")
    entrant_max = event.get("entrantSizeMax")
    team_roster = event.get("teamRosterSize")
    if isinstance(team_roster, dict):
        min_players = team_roster.get("minPlayers")
        max_players = team_roster.get("maxPlayers")
        if min_players and min_players != 1:
            return False
        if max_players and max_players != 1:
            return False
    elif team_roster and team_roster != 1:
        return False
    if entrant_min is not None and entrant_min != 1:
        return False
    if entrant_max is not None and entrant_max != 1:
        return False
    return True


def collect_player_results_for_tournaments(
    client: StartGGClient,
    tournaments: List[Dict],
    singles_only: bool = True,
    target_videogame_id: Optional[int] = None,
    store: Optional[SQLiteStore] = None,
    offline_only: bool = False,
) -> List[PlayerEventResult]:
    """Collect player event results across a list of tournaments."""
    records: List[PlayerEventResult] = []
    for tournament in tournaments:
        tournament_id = tournament.get("id")
        if tournament_id is None:
            continue
        events = fetch_tournament_events(
            client,
            tournament_id=int(tournament_id),
            store=store,
            offline_only=offline_only,
        )
        for event in events:
            if singles_only and not is_singles_event(event):
                continue
            if target_videogame_id is not None:
                event_game = event.get("videogame") or {}
                event_game_id = event_game.get("id")
                if str(event_game_id) != str(target_videogame_id):
                    continue
            bundle = collect_event_bundle(
                client,
                event,
                store=store,
                offline_only=offline_only,
            )
            records.extend(build_player_event_results(bundle))
    return records
