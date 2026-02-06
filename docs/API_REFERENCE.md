# SmashDA API Reference

This document is the canonical reference for the public Smash Data Analytics API. It is written for an AI agent that will be calling the API on behalf of a human user.

## Base URL

All requests should be made to:

`https://server.cetacean-tuna.ts.net`

## Authentication

None. This API is public and does not require tokens or API keys.

## Data Freshness and Endpoint Priority

This API exposes two classes of endpoints:

- **Precomputed endpoints** (`/precomputed`, `/precomputed_series`) are fast, cached, and the default choice for almost all user questions.
- **High-Intensity endpoints** (`/search`, `/search/by-slug`) run the full analytics pipeline and are slower and more expensive.

Guidance for AI usage:

- **Default to precomputed endpoints** for player stats, rankings, series summaries, and general queries.
- **Only use High-Intensity endpoints** if the user explicitly asks for granular or raw data that is not available in precomputed tables, or if the user requests a custom filter that is not supported by precomputed endpoints.

## Rate Limiting

The API enforces a simple per-IP sliding window rate limiter.

- Default limit: `60 requests per 60 seconds`
- Exceeding the limit returns `HTTP 429` with a `Retry-After` header.
- The response body includes a `detail` string describing the limit and next steps.

## Error Handling

Common error responses:

- `400` invalid parameter values (e.g., malformed dates or missing required fields)
- `404` no data found for the provided parameters
- `412` precomputed data is missing required columns for requested filters
- `429` rate limit exceeded
- `502` upstream start.gg / pipeline error (applies to High-Intensity endpoints)
- `500` server misconfiguration (e.g., missing start.gg token on the server)

## Common Concepts

### Time Windows

Most endpoints accept a rolling time window.

- `months_back` (default `6`) defines the rolling window.
- `window_offset` shifts the window into the past without changing its size.
- `window_size` overrides the window size in months.

### Tournament Filters

- `tournament_contains` and `tournament_slug_contains` are substring filters.
- `tournament_slug` is for exact slug matching (`tournament/genesis-9`).
- `start_date` and `end_date` override the rolling window and use `YYYY-MM-DD`.

### Player Filters

Filters can be applied on home state, average entrants, max event entrants, large event share, and most recent event timestamp.

### Tournament Lookup Notes

- `/tournaments` and `/tournaments/by-slug` are generally lower intensity than `/search` endpoints because they return tournament metadata only.
- `/tournaments/by-slug` fetches tournament details by slug/URL and should not be treated as precomputed-player-metrics cache access.

### High-Intensity Warnings

Endpoints under `/search` are **High-Intensity** and should be used sparingly:

- `/search`
- `/search/by-slug`

These endpoints trigger live pipeline computation and may be slow.

⚠️ **IMPORTANT:** `/search/by-slug` is often misused for simple tournament lookups. Use it ONLY when you need player analytics/statistics for specific tournaments. For basic tournament information (dates, location, attendance), use `/tournaments` or `/tournaments/by-slug` instead - these are low-intensity and much faster.

---

# OpenAPI-Style Reference

## GET `/health`

Simple liveness check.

### Response Schema

- `ok` (boolean) – Always `true` if the service is alive.

### Example Response

```json
{"ok": true}
```

---

## GET `/precomputed`

Precomputed player metrics for a state and time window.

### Query Parameters

- `state` (string, required) – Two-letter state/region code.
- `months_back` (integer, optional, default `6`, range `1–24`).
- `all_time` (boolean, optional, default `false`) – When `true`, returns the all-time precompute slice and overrides `months_back` to `0`.
- `videogame_id` (integer, optional, default `1386` for Ultimate, `1` for Melee).
- `character` (string, optional, default `"Marth"`).
- `limit` (integer, optional, default `50`, range `0–500`). `0` returns all rows.
- `filter_state` (string, repeatable) – Filter by `home_state`.
- `min_entrants` (integer, optional) – Minimum average event entrants.
- `max_entrants` (integer, optional) – Maximum average event entrants.
- `min_max_event_entrants` (integer, optional) – Minimum largest event entrants.
- `min_large_event_share` (float, optional, `0.0–1.0`) – Minimum large event share.
- `start_after` (string, optional) – `YYYY-MM-DD` for latest event cutoff.

### Response Schema

Top-level:

- `state` (string)
- `character` (string)
- `months_back` (integer)
- `all_time` (boolean)
- `videogame_id` (integer)
- `count` (integer)
- `results` (array of `PrecomputedPlayerMetric`)

`PrecomputedPlayerMetric` fields:

- `player_id` (integer)
- `gamer_tag` (string)
- `weighted_win_rate` (number | null)
- `opponent_strength` (number | null)
- `avg_seed_delta` (number | null)
- `upset_rate` (number | null)
- `activity_score` (number | null)
- `home_state` (string | null)
- `home_state_inferred` (boolean)
- `avg_event_entrants` (number | null)
- `max_event_entrants` (number | null)
- `large_event_share` (number | null)
- `latest_event_start` (integer | null) – Unix timestamp (seconds).
- `computed_at` (integer | null) – Unix timestamp when metrics were precomputed.

### Example Response

```json
{
  "state": "GA",
  "character": "Marth",
  "months_back": 6,
  "videogame_id": 1386,
  "count": 2,
  "results": [
    {
      "player_id": 12345,
      "gamer_tag": "PlayerOne",
      "weighted_win_rate": 0.71,
      "opponent_strength": 0.14,
      "home_state": "GA",
      "home_state_inferred": false,
      "avg_event_entrants": 84.2,
      "max_event_entrants": 152,
      "large_event_share": 0.58,
      "latest_event_start": 1738281600
    }
  ]
}
```

---

## GET `/precomputed_series`

Precomputed metrics scoped to a specific tournament series in a state.

### Query Parameters

- `state` (string, required)
- `months_back` (integer, optional, default `6`, range `1–24`)
- `all_time` (boolean, optional, default `false`) – When `true`, returns the all-time precompute slice and overrides `months_back` to `0`.
- `videogame_id` (integer, optional, default `1386`)
- `window_offset` (integer, optional, default `0`)
- `window_size` (integer, optional)
- `series_key` (string, optional) – Exact series key.
- `tournament_contains` (string, repeatable) – Name term to resolve a series key. **Note:** only the first value is used for series matching.
- `tournament_slug_contains` (string, repeatable) – Slug term to resolve a series key. **Note:** only the first value is used for series matching.
- `allow_multi` (boolean, optional, default `false`) – If true, returns multiple series matches.
- `limit` (integer, optional, default `50`, range `0–500`)
- `filter_state` (string, repeatable)
- `min_entrants` (integer, optional)
- `max_entrants` (integer, optional)
- `min_max_event_entrants` (integer, optional)
- `min_large_event_share` (float, optional)
- `start_after` (string, optional, `YYYY-MM-DD`)

### Response Schema

Top-level:

- `state` (string)
- `months_back` (integer)
- `all_time` (boolean)
- `videogame_id` (integer)
- `count` (integer)
- `results` (array of `PrecomputedSeriesMetric`)
- When `allow_multi=false` (default):
  - `series_key` (string)
  - `series_name_term` (string | null)
  - `series_slug_term` (string | null)
  - `resolved_label` (string)
- When `allow_multi=true` and multiple matches:
  - `series_keys` (array of string)
  - `resolved_labels` (array of string)

`PrecomputedSeriesMetric` fields:

- `player_id` (integer)
- `gamer_tag` (string)
- `weighted_win_rate` (number | null)
- `opponent_strength` (number | null)
- `avg_seed_delta` (number | null)
- `upset_rate` (number | null)
- `activity_score` (number | null)
- `home_state` (string | null)
- `home_state_inferred` (boolean)
- `avg_event_entrants` (number | null)
- `max_event_entrants` (number | null)
- `large_event_share` (number | null)
- `latest_event_start` (integer | null)
- `computed_at` (integer | null) – Unix timestamp when metrics were precomputed.
- `series_key` (string)
- `series_name_term` (string | null)
- `series_slug_term` (string | null)
- `series_label` (string)

### Example Response

```json
{
  "state": "GA",
  "months_back": 6,
  "videogame_id": 1386,
  "count": 1,
  "series_key": "battle-city-ult",
  "series_name_term": "battle city",
  "series_slug_term": null,
  "resolved_label": "battle city",
  "results": [
    {
      "player_id": 98765,
      "gamer_tag": "PlayerTwo",
      "weighted_win_rate": 0.68,
      "opponent_strength": 0.12,
      "home_state": "GA",
      "home_state_inferred": false,
      "avg_event_entrants": 64,
      "max_event_entrants": 120,
      "large_event_share": 0.5,
      "latest_event_start": 1738281600,
      "series_key": "battle-city-ult",
      "series_name_term": "battle city",
      "series_slug_term": null,
      "series_label": "battle city"
    }
  ]
}
```

---

## GET `/tournaments`

List tournaments in a window, optionally filtered by series name/slug.

### Query Parameters

- `state` (string, required)
- `months_back` (integer, optional, default `6`, range `1–24`)
- `videogame_id` (integer, optional, default `1386`)
- `window_offset` (integer, optional, default `0`)
- `window_size` (integer, optional)
- `limit` (integer, optional, default `50`, range `0–500`)
- `tournament_contains` (string, repeatable)
- `tournament_slug_contains` (string, repeatable)
- `tournament_slug` (string, repeatable) – exact slug match
- `start_date` (string, optional, `YYYY-MM-DD`)
- `end_date` (string, optional, `YYYY-MM-DD`)

### Response Schema

Top-level:

- `state` (string)
- `videogame_id` (integer)
- `months_back` (integer)
- `count` (integer)
- `results` (array of `TournamentSummary`)

`TournamentSummary` fields:

- `id` (integer | null)
- `slug` (string | null)
- `name` (string | null)
- `city` (string | null)
- `state` (string | null)
- `country` (string | null)
- `start_at` (integer | null) – Unix timestamp (seconds).
- `end_at` (integer | null) – Unix timestamp (seconds).
- `num_attendees` (integer | null)
- `name_matches` (array of string)
- `slug_matches` (array of string)

### Example Response

```json
{
  "state": "GA",
  "videogame_id": 1386,
  "months_back": 3,
  "count": 1,
  "results": [
    {
      "id": 123456,
      "slug": "tournament/battle-city-42",
      "name": "Battle City 42",
      "city": "Atlanta",
      "state": "GA",
      "country": "US",
      "start_at": 1735689600,
      "end_at": 1735776000,
      "num_attendees": 128,
      "name_matches": ["battle city"],
      "slug_matches": ["battle-city"]
    }
  ]
}
```

---

## GET `/tournaments/by-slug`

Lookup tournaments by exact slug or full start.gg URL.

### Query Parameters

- `tournament_slug` (string, repeatable, required) – slug or start.gg URL. Accepts any of these formats:
  - Slug: `tournament/genesis-9`
  - Full URL: `https://start.gg/tournament/genesis-9`
  - URL with extra path/params: `https://start.gg/tournament/genesis-9/event/melee-singles?foo=bar` (extra segments and query params are stripped)
  - Inputs that don't resolve to a `tournament/...` slug are returned in the `invalid` array.

### Response Schema

Top-level:

- `count` (integer)
- `results` (array of `TournamentSummaryBySlug`)
- `missing` (array of string) – slugs not found.
- `invalid` (array of string) – inputs that were not valid slugs or URLs.

`TournamentSummaryBySlug` fields:

- `id` (integer | null)
- `slug` (string | null)
- `name` (string | null)
- `city` (string | null)
- `state` (string | null)
- `country` (string | null)
- `start_at` (integer | null)
- `end_at` (integer | null)
- `num_attendees` (integer | null)

### Example Response

```json
{
  "count": 1,
  "results": [
    {
      "id": 123456,
      "slug": "tournament/genesis-9",
      "name": "Genesis 9",
      "city": "San Jose",
      "state": "CA",
      "country": "US",
      "start_at": 1675382400,
      "end_at": 1675555200,
      "num_attendees": 2400
    }
  ],
  "missing": [],
  "invalid": []
}
```

---

## GET `/search` (High-Intensity)

Runs the full analytics pipeline for a state and time window. Use sparingly.

### Query Parameters

- `state` (string, required)
- `character` (string, optional, default `"Marth"`)
- `months_back` (integer, optional, default `6`, range `1–24`)
- `videogame_id` (integer, optional, default `1386`)
- `window_offset` (integer, optional, default `0`)
- `window_size` (integer, optional)
- `assume_target_main` (boolean, optional, default `false`)
- `large_event_threshold` (integer, optional, default `32`)
- `limit` (integer, optional, default `25`, range `0–200`)
- `filter_state` (string, repeatable)
- `min_entrants` (integer, optional)
- `max_entrants` (integer, optional)
- `min_max_event_entrants` (integer, optional)
- `min_large_event_share` (float, optional)
- `start_after` (string, optional, `YYYY-MM-DD`)
- `tournament_contains` (string, repeatable)
- `tournament_slug_contains` (string, repeatable)
- `tournament_slug` (string, repeatable)
- `start_date` (string, optional, `YYYY-MM-DD`)
- `end_date` (string, optional, `YYYY-MM-DD`)

### Response Schema

Top-level:

- `state` (string)
- `character` (string)
- `count` (integer)
- `results` (array of `PlayerMetric`)

`PlayerMetric` fields:

- `player_id` (integer)
- `gamer_tag` (string)
- `state` (string | null) – explicit player state from start.gg.
- `events_played` (integer)
- `sets_played` (integer)
- `win_rate` (number | null)
- `weighted_win_rate` (number | null)
- `avg_seed_delta` (number | null)
- `opponent_strength` (number | null)
- `character_sets` (integer)
- `character_win_rate` (number | null)
- `character_weighted_win_rate` (number | null)
- `character_usage_rate` (number)
- `upset_rate` (number | null)
- `activity_score` (number)
- `tournaments_played` (integer)
- `latest_event_start` (integer | null)
- `avg_event_entrants` (number | null)
- `max_event_entrants` (number | null)
- `events_with_known_entrants` (integer)
- `large_event_threshold` (integer)
- `large_event_share` (number | null)
- `events_with_known_state` (integer)
- `inferred_state` (string | null)
- `inferred_state_confidence` (number | null)
- `home_state` (string | null)
- `home_state_inferred` (boolean)
- `home_state_confidence` (number | null)
- `home_country` (string | null)
- `home_country_inferred` (boolean)
- `home_country_confidence` (number | null)

### Example Response

```json
{
  "state": "GA",
  "character": "Marth",
  "count": 1,
  "results": [
    {
      "player_id": 12345,
      "gamer_tag": "PlayerOne",
      "state": "GA",
      "events_played": 12,
      "sets_played": 58,
      "win_rate": 0.66,
      "weighted_win_rate": 0.71,
      "avg_seed_delta": -1.8,
      "opponent_strength": 0.14,
      "character_sets": 18,
      "character_win_rate": 0.61,
      "character_weighted_win_rate": 0.66,
      "character_usage_rate": 0.31,
      "upset_rate": 0.27,
      "activity_score": 17.8,
      "tournaments_played": 9,
      "latest_event_start": 1738281600,
      "avg_event_entrants": 87,
      "max_event_entrants": 152,
      "events_with_known_entrants": 10,
      "large_event_threshold": 32,
      "large_event_share": 0.58,
      "events_with_known_state": 12,
      "inferred_state": "GA",
      "inferred_state_confidence": 0.75,
      "home_state": "GA",
      "home_state_inferred": false,
      "home_state_confidence": 1.0,
      "home_country": "US",
      "home_country_inferred": false,
      "home_country_confidence": 1.0
    }
  ]
}
```

---

## GET `/search/by-slug` (High-Intensity)

Compute player metrics for one or more specific tournament slugs.

### Query Parameters

- `tournament_slug` (string, repeatable, required) – slug or start.gg URL. Same format rules as `/tournaments/by-slug` (accepts full URLs, strips extra path segments and query params).
- `character` (string, optional, default `"Marth"`)
- `videogame_id` (integer, optional, default `1386`)
- `assume_target_main` (boolean, optional, default `false`)
- `large_event_threshold` (integer, optional, default `32`)
- `limit` (integer, optional, default `25`, range `0–200`)
- `filter_state` (string, repeatable)
- `min_entrants` (integer, optional)
- `max_entrants` (integer, optional)
- `min_max_event_entrants` (integer, optional)
- `min_large_event_share` (float, optional)
- `start_after` (string, optional, `YYYY-MM-DD`)
- `refresh` (boolean, optional, default `false`) – bypass cached SQLite data and refetch.
- `debug` (boolean, optional, default `false`) – include diagnostic info on events.

### Response Schema

Top-level:

- `slugs` (array of string)
- `character` (string)
- `count` (integer)
- `results` (array of `PlayerMetric`)
- `invalid` (array of string)
- `debug` (array of `TournamentDebugInfo`, only when `debug=true`)

`PlayerMetric` uses the same schema as `/search`.

`TournamentDebugInfo` fields (only if `debug=true`):

- `slug` (string)
- `tournament_id` (integer | null)
- `event_count` (integer)
- `events` (array of `TournamentEventSummary`)
- `error` (string, optional) – present when lookup failed.

`TournamentEventSummary` fields:

- `id` (integer | null)
- `name` (string | null)
- `slug` (string | null)
- `videogame_id` (integer | null)
- `is_singles` (boolean)
- `num_entrants` (integer | null)

### Example Response

```json
{
  "slugs": ["tournament/genesis-9"],
  "character": "Marth",
  "count": 1,
  "results": [
    {
      "player_id": 12345,
      "gamer_tag": "PlayerOne",
      "state": "CA",
      "events_played": 8,
      "sets_played": 36,
      "win_rate": 0.64,
      "weighted_win_rate": 0.7,
      "avg_seed_delta": -0.4,
      "opponent_strength": 0.22,
      "character_sets": 12,
      "character_win_rate": 0.58,
      "character_weighted_win_rate": 0.63,
      "character_usage_rate": 0.33,
      "upset_rate": 0.2,
      "activity_score": 11.6,
      "tournaments_played": 1,
      "latest_event_start": 1675382400,
      "avg_event_entrants": 1024,
      "max_event_entrants": 2400,
      "events_with_known_entrants": 8,
      "large_event_threshold": 32,
      "large_event_share": 1.0,
      "events_with_known_state": 8,
      "inferred_state": "CA",
      "inferred_state_confidence": 0.75,
      "home_state": "CA",
      "home_state_inferred": false,
      "home_state_confidence": 1.0,
      "home_country": "US",
      "home_country_inferred": false,
      "home_country_confidence": 1.0
    }
  ],
  "invalid": []
}
```

---

# How-To Guidance

## Recommended Default Workflow

1. Use `/precomputed` for general player ranking or performance questions.
2. Use `/precomputed_series` if the user asks for a specific series (e.g., "Battle City").
3. For tournament searches, use `/tournaments` with `tournament_contains` FIRST to discover exact slugs and handle ambiguous names.
4. Only use `/search` or `/search/by-slug` when a user explicitly requests raw or ultra-specific data not covered by precomputed metrics.

## Common Tasks

### Fetch top players in a state

Use `/precomputed` with `state` and `months_back`.

### Fetch players for a specific tournament series

Use `/precomputed_series` with `tournament_contains` or `tournament_slug_contains`.

### Find tournaments matching a series name

Use `/tournaments` with `tournament_contains` and/or `tournament_slug_contains`.

### Inspect a specific tournament

Use `/tournaments/by-slug` with a slug like `tournament/genesis-9`.

### Rare: compute live metrics for custom criteria

Use `/search` or `/search/by-slug` only when the user explicitly requests live computation or raw filtering that precomputed endpoints cannot satisfy.

---

## Tournament Search Strategy

### Recommended Workflow for Tournament Queries

When a user asks about tournaments, follow this three-step approach to minimize API load and maximize efficiency:

**Step 1: Use `/tournaments` with `tournament_contains` to discover tournaments**
- This is a **low-intensity** operation that helps identify exact slugs
- Handles ambiguous names gracefully by returning all matches
- Example (illustrative): searching for "4o4" may return both "4o4 weeklies" and "4o4 monthlies"
- Example (illustrative): searching for "battle city" may return a single matching series
- **This should be your FIRST step** for any tournament name search

**Step 2: Use `/tournaments/by-slug` for basic tournament information**
- Once you have an exact slug from Step 1, use this for tournament details
- Returns dates, location, attendance, and basic metadata
- Still **low-intensity** compared to analytics endpoints
- Use this when the user asks: "When was X tournament?", "Where was X held?", "How many people attended?"

**Step 3: Only use `/search/by-slug` if player analytics are needed**
- This is **HIGH-INTENSITY** and triggers full pipeline computation
- Use ONLY when the user explicitly needs player metrics/statistics for that specific tournament
- Example: "Show me player stats for Genesis 9" → requires `/search/by-slug`
- Example: "Who performed best at Battle City?" → requires `/search/by-slug`
- **Do NOT use** for simple tournament info (use Step 2 instead)

### Tournament Name Resolution Strategy

When a user asks about a tournament by name, use this two-step resolution approach:

**Example 1: Ambiguous tournament name**
```
User: "Show me stats from 4o4"

1. Call: GET /tournaments?state=GA&tournament_contains=4o4
2. Response (example): Multiple matches ["4o4 Weeklies", "4o4 Monthlies"]
3. Action: Present options to user and ask for clarification
4. Once clarified: Use appropriate endpoint based on what they need
   - For series rankings: /precomputed_series
   - For specific tournament analytics: /search/by-slug
```

**Example 2: Unique tournament name**
```
User: "Show me Battle City rankings"

1. Call: GET /tournaments?state=GA&tournament_contains=battle%20city
2. Response (example): Single match "Battle City"
3. Action: Extract series information or slug
4. Call: GET /precomputed_series?state=GA&tournament_contains=battle%20city
   (Returns rankings for the Battle City series)
```

**Example 3: User has exact slug already**
```
User: "Tell me about tournament/genesis-9"

1. Call: GET /tournaments/by-slug?tournament_slug=tournament/genesis-9
2. Response (example): Tournament details (date, location, attendance)
3. If user asks for stats: GET /search/by-slug?tournament_slug=tournament/genesis-9
```

**Why this approach works:**
- Using `tournament_contains` first prevents wasted high-intensity calls
- Handles ambiguous tournament names gracefully with user feedback
- Minimizes API load by using appropriate endpoint for each use case
- Provides clear path from fuzzy search → exact match → analytics

### Tournament Query Decision Tree

Use this decision tree to determine which endpoint to call:

**User asks: "Tell me about [tournament name]" or "When was [tournament]?"**
```
→ Use /tournaments with tournament_contains to find slug
→ Use /tournaments/by-slug with the slug for details
→ Do NOT use /search/by-slug (no analytics needed)
```

**User asks: "Who won [tournament]?" or "Show me player stats at [tournament]"**
```
→ Use /tournaments with tournament_contains to find slug
→ Use /search/by-slug with the slug for analytics (HIGH-INTENSITY)
```

**User asks: "What tournaments happened recently in GA?"**
```
→ Use /tournaments with state filter and time window
→ Optionally filter with tournament_contains if they mention a series name
```

**User asks: "Show me rankings for [tournament series]"**
```
→ Use /precomputed_series with tournament_contains
→ This returns player rankings scoped to that series (low-intensity)
→ Do NOT use /search/by-slug unless they want a specific tournament date
```

**User asks: "Show me all tournaments with 'weekly' in the name"**
```
→ Use /tournaments with tournament_contains=weekly
→ Returns list of all matching tournaments
```

### Common Mistakes to Avoid

❌ **Don't:** Use `/search/by-slug` for "When was Genesis 9?"
✅ **Do:** Use `/tournaments/by-slug` instead

❌ **Don't:** Use `/search/by-slug` without first checking `/tournaments` for the correct slug
✅ **Do:** Use `/tournaments` with `tournament_contains` first when the name is ambiguous; if the user already provides an exact slug and wants analytics, call `/search/by-slug` directly

❌ **Don't:** Guess tournament slugs when the user provides a name like "4o4"
✅ **Do:** Use `/tournaments` with `tournament_contains` to discover all matching options

❌ **Don't:** Use `/search` with `tournament_contains` when `/precomputed_series` can answer the question
✅ **Do:** Default to `/precomputed_series` for series-based rankings

---

## Quick Reference: Endpoint Selection Guide

| User Request | Endpoint to Use | Intensity | Why |
|-------------|-----------------|-----------|-----|
| "Top players in GA" | `/precomputed` | Low | Cached, pre-computed rankings |
| "Best players at Battle City" | `/precomputed_series` | Low | Series-specific cached rankings |
| "Find tournaments named 4o4" | `/tournaments` + `tournament_contains` | Low | Fuzzy search, returns all matches |
| "When was Genesis 9?" | `/tournaments/by-slug` | Low | Basic metadata lookup |
| "Player stats from Genesis 9" | `/search/by-slug` | **High** | Full analytics computation |
| "Recent tournaments in GA" | `/tournaments` | Low | List with time filter |
| "Custom filters not in precomputed" | `/search` | **High** | Live computation only |

**Golden Rule:** Always prefer low-intensity endpoints unless the user explicitly needs analytics that only high-intensity endpoints can provide.
