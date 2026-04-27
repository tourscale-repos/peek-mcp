# Peek MCP

Read-only MCP server for Peek Pro queries, fronted by the [`peek-app`](https://github.com/tourscale-repos/peek-app) credential broker. Lets agents query bookings, activities, and availability across all franchisee locations without ever touching install_ids or HMAC secrets.

## Architecture

```
agent → ts-peek-mcp (this) → ts-peek-app (broker, has secrets) → Peek Pro GraphQL
```

`peek-app` resolves a TourScale location slug (e.g. `cruisin-tikis-charleston`) to a Peek install_id, signs the JWT, and proxies the GraphQL call. The MCP only ever holds a Bearer token to peek-app's internal API.

## What it exposes

| Tool | Purpose |
|---|---|
| `health` | Probe — counts active installs |
| `list_locations` | All installed locations (slug, status, timestamps) |
| `get_location(slug)` | One install — 410 if uninstalled, 404 if not found |
| `peek_graphql(slug, query, variables_json)` | Generic GraphQL escape hatch |
| `current_account(slug)` | Verify a slug is wired up — returns Peek account name |
| `list_activities(slug)` | Bookable products at a location (id, name, type, resourceOptions) |
| `list_bookings(slug, first=20)` | Recent bookings with guest, ticket counts, activity, time window |
| `availability(slug, activity_id, resource_option_id, days_ahead, quantity)` | Forward availability + pricing window |

## Deployment

- Container: `ts-peek-mcp` on `tourscale-net`
- Port: `127.0.0.1:8098` (loopback only)
- Talks to `ts-peek-app:4000` over the internal docker network
- Auth: Bearer `PEEK_APP_INTERNAL_TOKEN` (master env)

## Local dev

```bash
PEEK_APP_URL=http://localhost:4000 \
PEEK_APP_INTERNAL_TOKEN=... \
MCP_TRANSPORT=http \
python3 server.py
```

## How this replaces the legacy 60+ keys

Master env still has `PEEK_PARTNER_ID_*` and `PEEK_WIDGET_*` per-location entries. As franchisees migrate from manually-shared API keys to installing the TourScale app from the Peek Pro App Store, those entries become deprecated. This MCP only uses the slug-based path; once every location is installed via the app store, those env entries can be deleted.
