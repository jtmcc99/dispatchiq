# DispatchIQ: Agentic Operations Assistant for Last-Mile Delivery

**[Live Demo](your-vercel-url-here)**

## The Problem

In last-mile delivery operations, the ops manager can be the single point of failure. Every exception — late orders, missing items, driver call-outs, coverage gaps — runs through one person's brain. When things get busy, manual processes get dropped. The customer service team only finds out about a problem if the ops manager physically walks over and tells them. Critical items ship without customers being notified they're missing. Incoming shift managers inherit chaos with no structured briefing.

**DispatchIQ replaces the human bottleneck with an AI agent that monitors, decides, and acts.**

## How It Works

DispatchIQ is a full-stack operations dashboard powered by an agentic AI system. The agent continuously monitors order flow and takes action when it detects problems.

### Real-Time Exception Detection
The agent calculates risk across every delivery window: orders remaining vs. time left vs. available drivers per zone. When the math doesn't work, it flags the problem before it becomes a missed delivery.

### Coverage Gap Analysis
Drivers are categorized by type (biker vs. driver) and zone (Uptown, Midtown, Chelsea, East Village, Downtown). When a driver calls out sick, the agent immediately identifies which zones are uncovered and which delivery windows are at risk — then recommends reallocation.

### Missing Item Escalation
When an item is missing during picking, the agent evaluates criticality. If it's a core item (like the main protein in a meal order), the agent blocks dispatch and generates a customer notification with a substitution or refund offer. Minor missing items get logged and communicated post-delivery. No order ships with a missing core item without the customer knowing.

### CS Notification Queue
Instead of the ops manager walking across the floor to tell customer service about a problem, the agent auto-generates notifications with order details, what happened, and a suggested customer communication script. CS marks each one as handled.

### Shift Summary
At any point, the agent generates a structured briefing: orders completed, orders late, delivery window progress, open exceptions, and unresolved CS items. An incoming shift manager reads this in 30 seconds and knows exactly what they're inheriting.

## Why This Matters

This tool was built from firsthand experience managing last-mile delivery operations. The problems it solves are real:

- **The "chicken breast problem":** A customer orders four items. The one thing that's missing is the main item. The order ships anyway because nobody had time to flag it. The customer gets a bag of sides with no main course. DispatchIQ catches this before dispatch.
- **The "coverage gap problem":** You have 7 bikers but 0 drivers for downtown. Downtown orders are too far for bikers. You don't realize the gap until orders start going late. DispatchIQ flags coverage gaps the moment a driver calls out.
- **The handoff problem:** The incoming manager gets a verbal briefing and a Slack scroll. Critical context gets lost. DispatchIQ generates a structured shift summary so nothing falls through the cracks.

## MCP Server

DispatchIQ ships with a [Model Context Protocol](https://modelcontextprotocol.io) server alongside the FastAPI backend. **MCP is the emerging standard for letting AI assistants safely call into your application's tools and data** — think of it as the USB-C port for LLMs. Instead of every product re-implementing tool use against every model vendor's proprietary API, you write one MCP server and *any* compliant client (Claude Desktop, Claude Code, Cursor, custom agents) can drive it.

DispatchIQ exposes one because the same operational reasoning that powers the in-app agent — flagging missing items, checking driver coverage, generating shift summaries — is more valuable when an ops manager can invoke it from wherever they already work (their IDE, their assistant, a custom workflow) rather than only inside this app.

> 🎥 **[Watch the 60-second demo →](ADD_LOOM_LINK_HERE)**

### Tools

| Name | What it does |
|------|--------------|
| `flag_missing_item(order_id, item_name)` | Returns a structured `MissingItemAssessment` telling the caller whether a missing item is a core item (immediate CS notification, block dispatch) or minor (batched at pick completion). |

*Remaining tools (`check_window_risk`, `check_driver_coverage`, `check_driver_reservation`, `create_exception`, `generate_cs_notification`, `generate_shift_summary`) follow the same pattern and are next on the porting roadmap — see [issue #N](link-to-tracker).*

### Resources

*None exposed yet.* Planned: `dispatchiq://orders`, `dispatchiq://orders/{id}`, `dispatchiq://drivers`, `dispatchiq://drivers/{id}` — the four read-only datasets the agent re-reads on every cycle, which belong as resources rather than tools.

### Quick start

**Prereqs:** [uv](https://docs.astral.sh/uv/) and an MCP-compatible client (Claude Desktop, Claude Code, or the MCP Inspector for ad-hoc testing).

```bash
cd mcp_server
uv sync
```

Run with the MCP Inspector (interactive testing in your browser):
```bash
uv run mcp dev server.py
```

Or run the bare stdio server (for a client to attach to):
```bash
uv run server.py
```

**Claude Desktop config** (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

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

Once configured, ask Claude something like *"There's an order ORD-CORE01 missing Pork Tenderloin — flag it"* and it will call the tool directly.

### What this demonstrates

- **Tool schemas derived from Python type hints, not hand-written JSON.** Strong types on every parameter and a Pydantic model for the return value mean FastMCP generates a precise, validated schema automatically — Claude sees a typed contract, not a free-form blob. Renaming a field in code updates the schema the model sees in the same commit.
- **Structured returns over plain errors and stringly-typed dicts.** Every tool returns a Pydantic model with documented fields; failures raise typed exceptions that surface to the client as real tool errors. The model can reason over the response shape instead of pattern-matching on prose.
- **Right primitive for the job: resource vs. tool.** Read-only datasets the agent re-fetches every cycle (orders, drivers) belong as MCP resources, not tools — they're addressable, cacheable, and don't burn a tool call. Mutations and decisions (flag, create exception, notify CS) stay as tools. Drawing that line up front is the design judgment MCP rewards.
- **Stdio transport with zero hosting overhead.** The server is a single Python file run by `uv` as a subprocess of the client — no HTTP, no auth layer, no deploy target. The in-product backend stays on FastAPI; the MCP surface is a thin, separately-versioned process that reuses the same data layer.

## Tech Stack

- **Backend:** FastAPI (Python) with Anthropic Claude API for agent intelligence
- **Frontend:** React (TypeScript) with Vite
- **Agent Architecture:** Claude tool use for exception detection, CS notification generation, coverage analysis, and shift summaries
- **MCP Server:** FastMCP (stdio transport), exposing the same tools to any MCP-compatible client
- **Storage:** JSON-based (orders, drivers, exceptions)

## Agent Capabilities

| Capability | What it does | How it decides |
|------------|--------------|----------------|
| Late risk detection | Flags orders at risk of missing their delivery window | Orders remaining × time left × available drivers |
| Coverage gap detection | Identifies zones without enough drivers | Driver type + zone assignment + call-outs |
| Missing item escalation | Blocks dispatch of orders missing core items | Item criticality assessment |
| CS notification | Generates customer communication scripts | Exception type + severity + order details |
| Smart driver reservation | Warns before assigning a driver to an order a biker could handle | Order size/weight + remaining driver pool + upcoming order queue |
| Shift summary | Creates end-of-shift briefing | Aggregates all metrics + open exceptions |

## Screenshots

Demo data only — safe to share publicly.

| Operations dashboard | CS notification queue | Shift summary |
|----------------------|-----------------------|---------------|
| Dashboard | CS queue | Shift summary |

Deep links (for docs or sharing a specific view): `?tab=dashboard`, `?tab=cs-queue`, `?tab=shift-summary`.

To regenerate images locally (Chrome on macOS, with the API and Vite already running): `./scripts/capture-readme-screenshots.sh`

## Setup

### Prerequisites

- Python 3.9+
- Node.js 18+
- Optional: An [Anthropic API key](https://console.anthropic.com) — only needed for Run Agent and other Claude-powered features. The dashboard loads with bundled demo data without it.

### Installation

```bash
git clone https://github.com/jtmcc99/dispatchiq.git
cd dispatchiq

python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

cd frontend
npm install
```

### Run

The frontend reads its API base URL from `VITE_API_URL` and falls back to `http://localhost:8000` in development, so running both servers locally just works with no extra config. Start the backend first, then the frontend.

```bash
# Terminal 1 — API
source venv/bin/activate
cd backend
uvicorn main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2 — UI (from repo root)
cd frontend
npm run dev
```

Open the URL Vite prints (usually `http://localhost:5173`). If that port is busy, Vite picks the next port — use the Local URL from the terminal.

**Anthropic key (optional):** To use Run Agent, set the key in the same shell before `uvicorn`:

```bash
export ANTHROPIC_API_KEY="your-key"
```

Never commit API keys. This repo ignores `.env` files; keep secrets in environment variables or a local `.env` that stays on your machine.

### Environment variables

| Variable | Where | Purpose |
|----------|-------|---------|
| `ANTHROPIC_API_KEY` | Backend | Required for agent features (Run Agent, shift summary). Not needed to browse the dashboard with demo data. |
| `DISPATCHIQ_CORS_ORIGINS` | Backend | Comma-separated list of allowed browser origins. Defaults to `http://localhost:5173, http://localhost:3000` when unset. Set to your Vercel URL in production. |
| `VITE_API_URL` | Frontend (build-time) | Base URL of the backend, e.g. `https://your-service.onrender.com`. Falls back to `http://localhost:8000` if unset. See `frontend/.env.example`. |

### Security note for public clones

All data in this repo is synthetic demo content. Do not add real customer names, addresses, or internal URLs to committed files.

## Deployment

This repo includes configuration for a Render (backend) + Vercel (frontend) deployment:

- `Procfile` — start command for Render/Heroku-style hosting
- `render.yaml` — Render Blueprint defining the Python web service, build command, and env var placeholders
- `frontend/.env.example` — template for the frontend `VITE_API_URL`

### Backend on Render

1. Render dashboard → New → Blueprint, select this repo
2. Render detects `render.yaml` and proposes the `dispatchiq-backend` web service (Python, free plan)
3. Set environment variables on the service:
   - `ANTHROPIC_API_KEY` — your Anthropic key (kept secret; marked `sync: false`)
   - `DISPATCHIQ_CORS_ORIGINS` — your Vercel URL (can be updated after the frontend is deployed)
4. Deploy and note the service URL, e.g. `https://dispatchiq-backend.onrender.com`

### Frontend on Vercel

1. Vercel dashboard → New Project, import the same repo
2. Set **Root Directory** to `frontend` (framework preset auto-detects Vite)
3. Add environment variable: `VITE_API_URL` = your Render backend URL
4. Deploy and note the Vercel URL, e.g. `https://dispatchiq.vercel.app`

### Finalize CORS

Back in Render, update `DISPATCHIQ_CORS_ORIGINS` to the live Vercel URL (comma-separate multiple origins if needed) and redeploy.

## Changelog

### MCP server (May 2026)
- **Standalone MCP server** under `mcp_server/` exposing DispatchIQ's ops tools to any MCP-compatible client (Claude Code, Claude Desktop, Cursor, etc.) via the FastMCP SDK and stdio transport. First tool ported: `flag_missing_item`. Remaining tools on the porting roadmap.

### Deployment & polish (April 2026)
- **Render + Vercel deployment config:** Added `Procfile`, `render.yaml`, and `frontend/.env.example` so the app can deploy to Render (backend) and Vercel (frontend) with minimal setup.
- **Env-driven API URL:** Frontend reads `VITE_API_URL` (build-time) with a local fallback to `http://localhost:8000`.
- **Env-driven CORS:** Backend honors `DISPATCHIQ_CORS_ORIGINS` (comma-separated) for production origins; localhost stays allowed by default.
- **Dashboard layout fix:** Picking progress indicator now sits on its own line in the order row, so it no longer overlaps the Picking status badge on narrow viewports.
- **Public-repo hardening:** Added MIT LICENSE, screenshots under `docs/screenshots/`, screenshot capture script, and URL deep links.

### v2 — Product Iteration (April 2026)
Changes based on hands-on testing and operational experience:

- **Smart driver reservation:** Agent warns when assigning a driver to a small order would leave no drivers for upcoming large/heavy orders. Prevents the common mistake of burning your only driver on a delivery a biker could handle.
- **Batched CS notifications:** OOS items accumulate per order and send as one notification when picking is complete — unless it's a core item, which triggers an immediate alert. Customers get one call, not five.
- **Pick progress visibility:** Delivery windows show items picked vs. total (20/61), and individual orders show progress (2/7). Ops managers can see at a glance whether a window will make it.
- **Drivers grouped by company:** Reorganized from zone-based to company-based grouping, reflecting how staffing actually works when everyone ships from one warehouse.
- **Expected vs. present staffing:** Replaced "X out sick" with "Expected: 12 | Present: 11 | Out: 1" for immediate clarity on staffing levels.
- **Large/heavy order flagging:** Orders requiring a driver (20+ items or heavy goods) are flagged before dispatch so they don't get assigned to a biker.
- **Shift summary redesign:** Replaced markdown wall with scannable card layout — critical issues at top, progress bars for delivery windows, numbered priorities for next shift.

## What's Next

- [ ] Port remaining tools to the MCP server (`check_window_risk`, `check_driver_coverage`, `create_exception`, etc.)
- [ ] Expose orders and drivers as MCP resources
- [ ] Historical analytics: track exception patterns over days/weeks
- [ ] Driver performance tracking
- [ ] Predictive late risk using historical delivery times
- [ ] SMS/push notifications for real-time driver communication
- [ ] Multi-location support

## Background

Built from firsthand experience managing delivery operations at a startup where the entire exception management process ran through one person's brain, Slack messages, and word of mouth. DispatchIQ demonstrates how agentic AI can replace human bottlenecks in real-time operations — not by removing the human, but by giving them an intelligent system that monitors, decides, and acts alongside them.

## License

See [LICENSE](./LICENSE) (MIT).
