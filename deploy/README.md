# Production deployment

The production layout keeps code, mutable data, secrets, logs, and backups separate:

- `/opt/smashDA`: application checkout and virtual environment
- `/var/lib/smashapi/smash.db`: authoritative SQLite database
- `/var/lib/smashapi/reports`: generated per-state CSV reports
- `/var/lib/smashapi/cache/startgg`: start.gg response cache and stale archives
- `/etc/smashapi/smashapi.env`: root-owned secrets and configuration
- `/var/log/smashapi`: refresh logs
- `/var/backups/smashapi`: timestamped, integrity-checked weekly backups

The API listens only on `127.0.0.1:8000`; a TLS reverse proxy such as Caddy should
provide public access. `smashapi-refresh.timer` runs the existing ingestion and
precomputation pipeline nightly. `smashapi-backup.timer` creates a consistent SQLite
snapshot every week and retains backups for 70 days by default.

The local retention policy protects against database corruption and accidental changes.
For host-loss protection, replicate `/var/backups/smashapi` to object storage or another
machine.
