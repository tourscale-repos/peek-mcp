#!/usr/bin/env python3
"""TourScale Peek Pro MCP — query Peek Pro per franchisee location via the
peek-app credential broker.

Architecture: peek-app (Phoenix, ts-peek-app:4000) is the canonical store of
Peek install credentials. Each franchisee installs the TourScale Peek Pro
App Store app, picks their location slug, and peek-app records the binding.
This MCP talks to peek-app's internal API (Bearer-token-authed) so HMAC
secrets and install_ids never leave that service.

The 60+ legacy `PEEK_API_KEY_*` per-location keys in master env are
deprecated by this flow; they're only used until every franchisee migrates
to the app-store install.
"""
import json
import os
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from mcp.server.fastmcp import FastMCP

_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.0,
        send_default_pii=False,
        release=os.environ.get("SENTRY_RELEASE", "peek-mcp@dev"),
    )

mcp = FastMCP("peek")

PEEK_APP_URL = os.environ.get("PEEK_APP_URL", "http://ts-peek-app:4000").rstrip("/")
INTERNAL_TOKEN = os.environ["PEEK_APP_INTERNAL_TOKEN"]


def _headers(json_body: bool = False) -> dict:
    h = {
        "Authorization": f"Bearer {INTERNAL_TOKEN}",
        "Accept": "application/json",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _call(method: str, path: str, *, params: dict | None = None, body: Any = None) -> Any:
    url = f"{PEEK_APP_URL}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url = f"{url}?{urlencode(clean)}"
    data = json.dumps(body).encode() if body is not None else None
    try:
        req = Request(url, data=data, headers=_headers(json_body=data is not None), method=method)
        with urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")
        return {"_error": e.code, "_body": body_txt[:600]}
    except URLError as e:
        return {"_error": "URLError", "_body": str(e)}


def _fmt(resp: Any) -> str:
    if isinstance(resp, dict) and "_error" in resp:
        return f"ERROR {resp['_error']}: {resp.get('_body', '')}"
    return json.dumps(resp, indent=2, default=str)


def _gql(slug: str, query: str, variables: dict | None = None) -> Any:
    return _call(
        "POST",
        f"/api/installs/{quote(slug, safe='')}/peek-graphql",
        body={"query": query, "variables": variables or {}},
    )


# ─── Probe + install management ──────────────────────────────────────────────

@mcp.tool()
def health() -> str:
    """Probe peek-app reachability and auth (lists active installs)."""
    res = _call("GET", "/api/installs")
    if isinstance(res, dict) and "_error" in res:
        return f"DOWN — {res['_error']}: {res.get('_body', '')}"
    n = len(res.get("installs", []))
    return f"OK — peek-app {PEEK_APP_URL} reachable, {n} active installs"


@mcp.tool()
def list_locations() -> str:
    """List all active Peek Pro installs (one per franchisee location). Returns slug, status, installed_at, last_authenticated_at."""
    return _fmt(_call("GET", "/api/installs"))


@mcp.tool()
def get_location(slug: str) -> str:
    """Get one install by TourScale location slug (e.g. 'cruisin-tikis-charleston'). Returns 410 if uninstalled, 404 if not found."""
    return _fmt(_call("GET", f"/api/installs/{quote(slug, safe='')}"))


# ─── Generic GraphQL proxy ───────────────────────────────────────────────────

@mcp.tool()
def peek_graphql(slug: str, query: str, variables_json: str | None = None) -> str:
    """Run an arbitrary Peek Pro GraphQL query for one location. The slug must match an installed location. variables_json is an optional JSON object string. peek-app handles the install-id resolution + HMAC signing — those secrets never enter this MCP."""
    variables = json.loads(variables_json) if variables_json else {}
    return _fmt(_gql(slug, query, variables))


# ─── Curated convenience queries ─────────────────────────────────────────────

@mcp.tool()
def current_account(slug: str) -> str:
    """Probe Peek for the location's current account name and id — useful for verifying a slug is wired up correctly."""
    q = "query GetCurrentAccount { currentAccount { id name } }"
    return _fmt(_gql(slug, q))


@mcp.tool()
def list_activities(slug: str) -> str:
    """List bookable activities (products) for a location. Each activity has id, name, type, and resourceOptions (capacity tiers)."""
    q = """
      query Products {
        activities {
          id
          name
          type
          resourceOptions { id name }
        }
      }
    """
    return _fmt(_gql(slug, q))


@mcp.tool()
def list_bookings(slug: str, first: int = 20) -> str:
    """List recent bookings for a location (Peek's `sales` connection filtered to type=BOOKING). Returns guest name, ticket counts, activity, and time window for each."""
    q = """
      query Bookings($first: Int!, $filter: SalesFilter!) {
        sales(first: $first, filter: $filter) {
          pageInfo { hasNextPage endCursor }
          edges {
            node {
              ... on Booking {
                primaryGuest { name }
                ticketQuantities {
                  resourceOptionSnapshot { name }
                  quantity
                }
                activitySnapshot { id name }
                timeSnapshot { from until }
              }
            }
          }
        }
      }
    """
    variables = {"first": first, "filter": {"types": ["BOOKING"]}}
    return _fmt(_gql(slug, q, variables))


@mcp.tool()
def availability(
    slug: str,
    activity_id: str,
    resource_option_id: str,
    days_ahead: int = 14,
    quantity: int = 1,
) -> str:
    """Pull availability + pricing for an activity over the next N days. Use `list_activities` first to find activity_id and resource_option_id."""
    from datetime import date, timedelta
    today = date.today()
    end = today + timedelta(days=days_ahead)
    range_str = f"[{today} 00:00:00,{end} 00:00:00)"
    q = """
      query Availability($input: AvailabilityRequestInput!) {
        availabilityDates(availabilityRequest: $input) {
          date
          availabilityTimes {
            time
            availability { resourceOptionId qty taken }
            prices {
              resourceOptionId
              pricing { price { amount currency } }
            }
          }
        }
      }
    """
    variables = {
        "input": {
            "activityId": activity_id,
            "dateRange": range_str,
            "resourceOptionQuantities": [
                {"resourceOptionId": resource_option_id, "quantity": quantity}
            ],
        }
    }
    return _fmt(_gql(slug, q, variables))


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        port = int(os.environ.get("MCP_PORT", "8000"))
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port
        mcp.settings.transport_security.enable_dns_rebinding_protection = False
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
