# smash-character-competency

Toolkit for exploring Super Smash Bros. player competency through the start.gg GraphQL API. The codebase pulls regional tournament data, stitches together seeds/standings/sets, and surfaces player-level metrics that feed notebooks and visualizations.

## What it does

- Discovers recent tournaments for a state + videogame and caches the raw GraphQL responses locally.
- Normalizes entrant, placement, seed, and per-set character data to build a player-event timeline.
- Computes interpretable metrics (weighted win rates, upset rate, activity, character usage, inferred home state, etc.) that can be printed in the terminal or written to CSV for downstream analysis.
- Powers notebooks/plots (see `Visualizer.ipynb`) that turn the metrics into shareable visuals.

## How the pipeline works

1. `startgg_client.py` authenticates with start.gg (using `STARTGG_API_TOKEN`) and caches responses in `.cache/startgg` to stay within rate limits.
2. `smash_data.py` pages through tournaments, events, seeds, standings, and sets, assembling `PlayerEventResult` records with consistent structure.
3. `metrics.py` aggregates those records into per-player metrics, including optional character-specific splits.
4. `smash_analysis.py` and `run_report.py` tie it together for CLI usage (`generate_player_metrics`), notebooks, or scripted exports.

Because every layer is pure Python, you can import any module from notebooks or other projects without the CLI.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Export your start.gg API token (create one in the start.gg Developer Portal):

```bash
export STARTGG_API_TOKEN="<your_token_here>"
```

Run the unit tests (they mock out the network layer, so no token needed):

```bash
pytest -q
```

## Running the CLI

```bash
python run_report.py GA --character Marth --months-back 6
```

This command prints a table of the weighted metrics for Georgia Ultimate players in the last six months, highlighting Marth usage. Use `--output /path/to/report.csv` to save the full DataFrame.

### Example output

```
 gamer_tag state home_state home_state_inferred home_country events_played sets_played avg_event_entrants max_event_entrants large_event_share win_rate weighted_win_rate avg_seed_delta opponent_strength character_usage_rate
     Teaser    GA        GA               False          US            12          58               87               152             0.58    0.66             0.71        -1.8              0.14                 0.32
```

Note: values vary depending on the tournaments in scope.

## CLI flags

`run_report.py` accepts a set of filters to narrow the dataset:

- `state`: Required positional argument. Two-letter code used to discover tournaments.
- `--character`: Target character for usage/weighted metrics (default `Marth`).
- `--videogame-id`: start.gg videogame identifier (Ultimate = `1386`).
- `--months-back`: Rolling tournament discovery window (default `6`).
- `--window-offset`: Shift the window into the past without changing its size (e.g., `--window-size 1 --window-offset 2` fetches tournaments from 2–3 months ago so you can warm the cache chunk-by-chunk).
- `--window-size`: Override the window length in months (defaults to `--months-back`). Pair with `--window-offset` to iterate through month slices.
- `--tournament-contains` / `--tournament-slug-contains`: Only include tournaments whose name or slug contains one of the provided substrings (repeatable; case-insensitive). Handy for series-specific reports like “Battle City” or “4o4”.
- `--assume-target-main`: When a player has zero logged sets for the character, treat the target character as their main (assigns win rates from overall performance).
- `--filter-state`: Include only players whose home state (explicit or inferred) matches one of the provided codes. Repeat the flag to allow multiple states.
- `--min-entrants` / `--max-entrants`: Restrict players based on the average size of their events.
- `--min-max-event-entrants`: Keep players whose single largest event cleared the provided entrant count (useful when you only want competitors who have experienced majors).
- `--large-event-threshold`: Define what “large” means for the share filter (default `32` entrants).
- `--min-large-event-share`: Require at least this fraction of a player’s events to meet the large-event threshold (e.g., `0.33` means one third of their brackets were that size). This is handy when you want consistent midsize/major activity while still allowing grassroots locals.
- `--start-after`: Drop players whose most recent event started before a specific date (`YYYY-MM-DD`).
- `--output`: Write the full metrics DataFrame to CSV instead of printing a subset of columns.

Use the average entrant filters when you care about overall bracket size, `--min-max-event-entrants` when you only want players who have touched events of a certain scale, and the large-event share combo when you need a middle ground (“they regularly play 64+ entrant brackets, but we still respect their 20-person locals”).

I may move this section to its own documentation if I add more functionality.

## Precomputing metrics for the API

If you want the FastAPI service to serve instant responses, persist a trimmed set of metrics (weighted win rate + opponent strength) into SQLite ahead of time:

```bash
python precompute_metrics.py --all-states --months-back 6 --character Marth
```

The script pulls every state already present in the `.cache/startgg/smash.db` `tournaments` table and recomputes metrics offline by using the cached seeds/standings/sets. Pass `--state GA --state FL` if you only want a subset. New rows live in the `player_metrics` table with a timestamp so you can refresh on a schedule or cron job.

Once the table is populated the API exposes a lightweight endpoint that never hits start.gg:

```bash
curl -G \
  --data-urlencode "state=GA" \
  --data-urlencode "character=Marth" \
  http://localhost:8000/precomputed
```

Parameters mirror the CLI flags (`state`, `months_back`, `videogame_id`, `character`, and `limit`). The response is a compact list of `{player_id, gamer_tag, weighted_win_rate, opponent_strength}` rows suitable for sending to browsers so each visitor can filter/plot locally without re-running the expensive pipeline.

You can now also apply the same filters exposed by the live `/search` endpoint—`filter_state`, entrant bounds, `min_max_event_entrants`, `min_large_event_share`, and `start_after`—so front ends can request pre-trimmed slices without rehydrating pandas. (If you precomputed metrics before this change, re-run `precompute_metrics.py` so the cached rows include the new metadata columns.)

### Warming specific months

If a bulk precompute run fails for a couple of states, run the CLI in month-sized slices to make sure SQLite contains the missing tournaments before retrying the precompute script:

```bash
# Fill just the window from 2–3 months ago for MA, MD, and MO.
python run_report.py MA --window-size 1 --window-offset 2 --months-back 3 --limit 0
python run_report.py MD --window-size 1 --window-offset 2 --months-back 3 --limit 0
python run_report.py MO --window-size 1 --window-offset 2 --months-back 3 --limit 0
```

Each invocation updates the local `.cache/startgg/smash.db` store without committing the generated CSV; afterwards `python precompute_metrics.py --state MA --state MD --state MO --months-back 3` will reuse that cache and finish instantly. Use different offsets (`0` = newest month, `1` = 1–2 months ago, etc.) to backfill whichever slices you missed.

`precompute_metrics.py` accepts the same `--window-size`/`--window-offset` flags, so if you really only need a historical slice in the database you can compute and persist it directly without touching the live window.

## Working with the metrics elsewhere

- Import `generate_player_metrics` or `generate_character_report` directly to consume DataFrames in notebooks or other scripts.
- Use `find_tournaments` for quick, in-notebook discovery of tournaments that match a series name/slug (e.g., `"battle city"` or `"4o4"`) before pulling the corresponding player metrics.
- Visualizations now ship with a Voilà-ready dashboard under `Visualizer.ipynb`. Launch it with `voila Visualizer.ipynb --port 8866` to expose controls for game/state/month filters, entrant thresholds (average, max-event, large-event share), adjustable “large” definitions, plus axis dropdowns (swap between weighted win rate, opponent strength, seed delta, upset rate, etc.). Click **Fetch metrics** to refresh the underlying DataFrame and the scatter plot/table update instantly.
- The first run hydrates a SQLite database at `.cache/startgg/smash.db` that stores tournaments, events, and per-event payloads. Follow-up runs read straight from the database (and only re-sync from start.gg once a week or when the date window expands), so you can explore older tournaments offline. Delete the file if you ever want to rebuild it from scratch, or pass `use_store=False` to `generate_player_metrics` for ephemeral environments.
- Raw GraphQL responses are no longer cached as JSON when the SQLite store is enabled. If you need the old behavior (e.g., for debugging schema changes), pass `use_store=False` or `use_cache=True` explicitly to `generate_player_metrics` to re-enable the hashed `.cache/startgg/*.json` snapshots (those still auto-refresh every seven days and archive the previous payload).
- Location attribution got more robust: we now infer a `home_state` only when at least three events with known states exist and one state accounts for ≥60% of them. Tournaments also record `addrCountry`, so we expose `home_country`/confidence columns alongside the state fields. Use these when filtering travelers vs. locals or when exploring regions outside the US.
- Series discovery & precompute: use `run_report.py --tournament-contains/--tournament-slug-contains` to scope a slice by series; `Visualizer.ipynb` and `Visualizer_api.ipynb` expose the same knobs for manual exploration. For fast API responses, precompute series metrics with `python precompute_metrics.py --state GA --months-back 3 --auto-series` (picks top N series by entrants plus any with max entrants ≥ threshold or event count ≥ threshold; defaults N=5, max≥32, events≥3). Results live in the `player_series_metrics` table.

## API usage

The FastAPI service in `api.py` exposes the same metrics over HTTP. Run it locally or on a Tailscale-accessible machine with:

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

### Endpoints

- `GET /health` – returns `{"ok": true}`; useful for load balancers and tunnels.
- `GET /precomputed` – serves cached weighted win rate/opponent strength rows (see above section).
- `GET /search` – runs the analytics pipeline and returns the top N player rows.
- `GET /precomputed_series` – serves cached series-scoped metrics; accepts `state`, `months_back`, `videogame_id`, `window_offset`, `window_size`, and either `series_key` or term selectors (`tournament_contains`/`tournament_slug_contains`). Supports the same filters as `/precomputed` (`filter_state`, entrant bounds, `start_after`). Response includes `resolved_label` to show which series matched your terms.
- `GET /tournaments` – returns tournaments in a window, optionally filtered by name/slug substrings (series search).
- `GET /tournaments` – returns tournaments in a window, optionally filtered by name/slug substrings (series search).

### Rate limiting

All HTTP endpoints share a lightweight per-IP rate limiter (default: 60 requests per 60 seconds). Tune the ceiling through `SMASHCC_RATE_LIMIT_REQUESTS` and `SMASHCC_RATE_LIMIT_WINDOW` environment variables or set the request count to `0` to disable the guard entirely. Exceeding the limit returns HTTP 429 with a `Retry-After` header so clients know when to retry.

#### Query parameters for `/precomputed`

| Param | Required | Description |
| ----- | -------- | ----------- |
| `state` | yes | Two-letter state/region used when metrics were precomputed. |
| `months_back` | optional, default `6` | Rolling window the persisted metrics cover. |
| `videogame_id` | optional, default `1386` | start.gg game ID the metrics were generated with. |
| `character` | optional, default `"Marth"` | Character emphasis used during the precompute step. |
| `limit` | optional, default `50` | Maximum rows to include (`0` streams everything). |
| `filter_state` | optional, repeatable | Keep players whose `home_state` matches any provided code. |
| `min_entrants` / `max_entrants` | optional | Filter by average event entrants. |
| `min_max_event_entrants` | optional | Require the player’s largest event to clear a threshold. |
| `min_large_event_share` | optional | Require at least this fraction of events to be “large” (`0.0–1.0`). |
| `start_after` | optional | ISO date (`YYYY-MM-DD`) that the player’s latest event must be on/after. |

Filtering happens after the rows are pulled from SQLite, so you can request generous limits (even `0`) once per UI load and cache the JSON client-side for quicker visual tweaks.

#### Query parameters for `/search`

| Param | Required | Description |
| ----- | -------- | ----------- |
| `state` | yes | Two-letter region to discover tournaments (e.g., `GA`). |
| `months_back` | optional, default `6` | Rolling discovery window. |
| `videogame_id` | optional, default `1386` | start.gg game ID (`1386` Ultimate, `1` Melee). |
| `limit` | optional, default `25` | Maximum rows to return (`0` returns all rows). |
| `tournament_contains` | optional, repeatable | Only include tournaments whose name contains one of these substrings (case-insensitive). |
| `tournament_slug_contains` | optional, repeatable | Only include tournaments whose slug contains one of these substrings (case-insensitive). |
| `large_event_threshold` | optional, default `32` | Entrant count that defines “large” when computing shares. |
| `assume_target_main` | optional, default `false` | Backfill character stats when sets are missing. |
| `character` | optional, default `"Marth"` | Legacy field; leave at default if you only care about overall stats. |
| `filter_state` | optional, repeatable | Keep players whose `home_state` matches any provided code. |
| `min_entrants` / `max_entrants` | optional | Filter by average event entrants. |
| `min_max_event_entrants` | optional | Require the player’s largest event to clear a threshold. |
| `min_large_event_share` | optional | Require at least this fraction of events to be “large” (`0.0–1.0`). |
| `start_after` | optional | ISO date (`YYYY-MM-DD`) that the player’s latest event must be on/after. |

`filter_state` can be repeated (`...&filter_state=GA&filter_state=AL`) to allow multiple regions.

Example: pull three months of Georgia data and keep the top 50 rows.

```bash
curl -G \
  --data-urlencode "state=GA" \
  --data-urlencode "months_back=3" \
  --data-urlencode "tournament_contains=battle city" \
  --data-urlencode "limit=50" \
  http://localhost:8000/search
```

Example with filters (two allowed home states, min entrant constraints, and unlimited rows):

```bash
curl -G \
  --data-urlencode "state=GA" \
  --data-urlencode "months_back=3" \
  --data-urlencode "limit=0" \
  --data-urlencode "filter_state=GA" \
  --data-urlencode "filter_state=AL" \
  --data-urlencode "min_entrants=32" \
  http://localhost:8000/search
```

Example using precomputed series metrics (requires running `precompute_metrics.py --auto-series` first). The `tournament_contains`/`tournament_slug_contains` terms are used to resolve a series key that was persisted during the auto-series pass:

```bash
curl -G \
  --data-urlencode "state=GA" \
  --data-urlencode "months_back=3" \
  --data-urlencode "tournament_contains=battle city" \
  http://localhost:8000/precomputed_series
```

Response shape:

```json
{
  "state": "GA",
  "character": "Marth",
  "count": 50,
  "results": [
    {
      "gamer_tag": "PlayerOne",
      "events_played": 12,
      "sets_played": 64,
      "weighted_win_rate": 0.73,
      "opponent_strength": 0.18,
      "avg_event_entrants": 84,
      "large_event_share": 0.42,
      "home_state": "GA",
      "home_state_inferred": false,
      "...": "additional columns omitted"
    }
  ]
}
```

The `results` array mirrors the DataFrame printed by `run_report.py`, so every column is available for plotting in notebooks or front-ends. The most useful axes for dot plots are:

- `weighted_win_rate` – combines win rate, event size, and recency. Higher means stronger recent performance.
- `opponent_strength` – average of `1/opponent_seed` (or placement) to approximate strength of schedule.
- `avg_event_entrants`, `max_event_entrants`, `large_event_share` – contextualize bracket size.
- `activity_score`, `events_played`, `sets_played` – show consistency and recent volume.

### Consuming from notebooks/UI

You can point pandas at the API without re-downloading tournaments locally:

```python
import pandas as pd

resp = pd.read_json("http://switch-tailscale-ip:8000/search?state=GA&months_back=3")
df = pd.DataFrame(resp["results"])
```

At that point you can filter by weighted win rate or opponent strength exactly like you would with the CLI DataFrame, but every device shares the same SQLite-backed cache.

## Development tips

- Respect start.gg rate limits; the built-in caching is there to keep repeated runs fast.
- When adding metrics, extend `PlayerAggregate` in `metrics.py` and update the column selection in `run_report.py`.
- If you change GraphQL shapes, update the integration tests under `tests/` to keep the mocked payloads in sync.

## Deployment notes

- For quick sharing, run the FastAPI service with `uvicorn smashcc.api:app --host 0.0.0.0 --port 8000` and front it with Voilà (`voila Visualizer.ipynb`) plus a Cloudflare Tunnel.
- On the Switch, verify the app in a virtual environment before exposing it: copy the repo, install from `requirements.txt`, set `STARTGG_API_TOKEN`, smoke test the CLI and API locally, then wire up the tunnel.
- Dockerizing is optional for the first iteration; ship a native install to gather feedback, then containerize once the API/notebook stabilize so future updates are reproducible.
