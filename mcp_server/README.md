# DispatchIQ MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes
DispatchIQ's operations tools to any MCP-compatible client (Claude Desktop, MCP
Inspector, custom agents).

The server shares the FastAPI backend's data layer — `backend/data_store.py`
and `backend/models.py` — by splicing `../backend` onto `sys.path` at import
time, so tool calls hit the same JSON-backed store the live demo uses.

## What this demonstrates

The MCP server reuses DispatchIQ's existing risk-assessment logic via a shared
`backend/risk.py` module — the same business rules drive the in-app agent and
the MCP surface, so the two interfaces can never disagree on what counts as a
critical window or an at-risk zone.

## Currently exposed

### Tools

All seven shipped. Lookup-failure tools return a structured
`NotFound` result (`kind="not_found"`) instead of raising, and same-order
exception creates dedupe on the spec-level `ExceptionType` so distinct
categories (e.g. `missing_core_item` vs. `missing_minor_item`) coexist.

| Name | Purpose | Returns |
|------|---------|---------|
| `flag_missing_item(order_id, item_name)` | Decide if a missing item is a core item (immediate CS notification) or minor (batched at pick completion). | `MissingItemAssessment \| NotFound` |
| `check_window_risk(window_id=None)` | Assess late-risk for one or all active delivery windows; returns a risk level and a recommendation per window. | `list[WindowRisk]` |
| `check_driver_coverage(zone=None)` | Identify which zones are covered / at risk / uncovered, broken out by biker vs. driver. | `list[ZoneCoverage] \| NotFound` |
| `check_driver_reservation(order_id, proposed_driver_id)` | Decide whether a proposed driver-to-order assignment is approve / warn / block, given remaining car-driver capacity. | `ReservationCheck \| NotFound` |
| `create_exception(type, severity, details, order_id=None)` | Mutation. Record a tracked operational exception. Dedupes per (order, spec-level type) so distinct issue categories on the same order don't collapse. | `ExceptionRecord` |
| `generate_cs_notification(order_id, exception_type, additional_context=None)` | Mutation. Generate a draft customer-facing script and persist a CS notification (immediate or batched per DispatchIQ policy). | `CSNotification \| NotFound` |
| `generate_shift_summary(shift_id=None)` | Read-only briefing: window progress, open exceptions, unresolved CS items, and top priorities for the next shift. | `ShiftSummary \| NotFound` |

### Resources

None yet. Planned: `dispatchiq://orders`, `dispatchiq://orders/{id}`,
`dispatchiq://drivers`, `dispatchiq://drivers/{id}`.

## Running

This project uses [uv](https://docs.astral.sh/uv/). All commands assume your
working directory is `mcp_server/`.

### Install dependencies

```bash
uv sync
```

### Run the server directly (stdio)

```bash
uv run server.py
```

The server speaks MCP over stdio; nothing useful happens unless a client is
attached.

### Run with the MCP Inspector (recommended for testing)

```bash
uv run mcp dev server.py
```

This launches the Inspector UI in your browser, spawns the server as a
subprocess, and lets you invoke tools interactively. To exercise the smoke
test, call `flag_missing_item` with an `order_id` from
`backend/data/orders.json` (e.g. `ORD-001`) and an `item_name` from that
order's `items` list.

## Client config

Same JSON shape works for Claude Desktop, Cursor, and Claude Code — only the
config file location differs. Replace `/ABSOLUTE/PATH/TO/dispatchiq` with the
absolute path to your local clone, then restart the client.

```json
{
  "mcpServers": {
    "dispatchiq": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/dispatchiq/mcp_server",
        "run",
        "server.py"
      ]
    }
  }
}
```

Config file locations:

- **Claude Desktop** (macOS): `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Cursor** (per-project): `.cursor/mcp.json` at the repo root; or globally `~/.cursor/mcp.json`
- **Claude Code**: `claude mcp add --transport stdio dispatchiq -- uv --directory /ABSOLUTE/PATH/TO/dispatchiq/mcp_server run server.py`

## Layout

```
mcp_server/
├── server.py        # FastMCP server + @mcp.tool() wrappers (thin layer)
├── _models.py       # Pydantic response types + ExceptionType enum
├── _assess.py       # Read-only assessment helpers (window risk, coverage, reservation)
├── _records.py      # Mutation + summary helpers (exception, CS notif, shift summary)
├── _path.py         # sys.path shim that adds ../backend
├── pyproject.toml   # uv project (mcp[cli], httpx)
└── README.md        # this file
```

The shared backend logic — `data_store`, `models`, and the pure
risk-classification helpers in `backend/risk.py` — is imported across the
`../backend` path shim. `backend/agent.py` re-exports the same risk helpers
so the FastAPI agent and the MCP server stay in lockstep.
