# Semiconductor Equipment Service Centre — Agentic Assistant

A conversational assistant for equipment engineers and service managers: prioritise
open tickets, investigate root causes, and generate structured follow-up notes,
grounded in purpose-built backend services rather than free-form LLM guessing.

See `docs/architecture.md` for the full design rationale and
`docs/superpowers/specs/2026-07-11-service-centre-agent-design.md` for the original
design spec.

## Prerequisites

- Docker and Docker Compose
- An Anthropic API key with access to `claude-sonnet-5` and `claude-haiku-4-5-20251001`
  (or set `CLAUDE_MODEL`/`CLAUDE_PLANNER_MODEL` to models your key has access to — see
  "Model tiering and cost bound" below for why two models are used)

## Setup

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY
```

## Run everything

```bash
docker compose up --build
```

This starts 5 containers: `ticket-service` (8001), `equipment-history-service` (8002),
`knowledge-service` (8003), `recommendation-service` (8004), `agent-orchestrator` (8000).
`agent-orchestrator` waits for the other four to report healthy before starting.

Open `http://localhost:8000/` for the chat UI, or call the API directly:

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Which open equipment tickets should I prioritise today and why?"}'
```

Retrieve the full tool-call trace behind any answer:

```bash
curl -s http://localhost:8000/audit/<request_id>
```

## Model tiering and cost bound

`agent-orchestrator` uses two Claude models, not one:

- `CLAUDE_PLANNER_MODEL` (default `claude-haiku-4-5-20251001`) — a single, cheap
  planning call per request that decides which tool(s) to call.
- `CLAUDE_MODEL` (default `claude-sonnet-5`) — the synthesis call that turns tool
  results into the structured answer.

Every request makes at most 3 Claude calls total: 1 plan + 1 synthesis, plus exactly
1 more capped "revision" synthesis call, only if the first synthesis judges its own
evidence insufficient. There is no open-ended tool-use loop and no repair round-trip —
see `docs/architecture.md` and the design spec's §3.1/§9.1 for why this bounded
"plan→execute→synthesise" shape was chosen over a live tool-use loop for a
cost-sensitive, auditable deployment context.

## Run a single service outside Docker (for development)

```bash
cd services/ticket-service
pip install -r requirements.txt -r requirements-test.txt --break-system-packages
DB_PATH=/tmp/tickets.db SEED_PATH=../../data/seed uvicorn app.main:app --reload --port 8001
```

Repeat the same pattern for the other services, substituting the relevant env vars
from `docker-compose.yml`.

## Run the tests

```bash
python -m pytest tests/test_seed_data.py -v
for svc in ticket-service equipment-history-service knowledge-service recommendation-service agent-orchestrator; do
  (cd services/$svc && pip install -r requirements.txt -r requirements-test.txt --break-system-packages && python -m pytest tests/ -v)
done
```

## Example queries to try

- "Which open equipment tickets should I prioritise today and why?"
- "For tool ETCH-07, summarise the recent alarm history and likely causes."
- "Compare this issue against similar historical cases and suggest next troubleshooting steps."
- "Generate a structured service follow-up note for the engineer."
- "Show me the evidence behind your recommendation." (follow-up in the same session, or open the audit trace link)

## Known limitations and assumptions

- No authentication/authorization is implemented; see the design spec's §8 "Security
  / access-control assumption"
  (`docs/superpowers/specs/2026-07-11-service-centre-agent-design.md`) for the
  documented production assumption.
- Knowledge retrieval is TF-IDF over 3 documents — adequate for this demo, not for a
  production-scale document corpus (see the design spec's §9 "Trade-offs & future
  production considerations").
- The agent can only draft follow-up notes; persisting one requires an explicit
  "Save follow-up" click in the UI (or a direct call to
  `POST /tickets/{ticket_id}/followups`) — this is a deliberate human-in-the-loop
  gate, not an oversight.
- Dependency versions in each `requirements.txt` are floors (`>=`), not exact pins;
  run `pip freeze > requirements.lock` per service if you need fully reproducible
  builds.

## What I'd improve with more time

- Replace TF-IDF with a real vector store for knowledge retrieval at scale.
- Add a circuit breaker around downstream calls instead of a flat retry-once policy.
- Implement the documented RBAC assumption (engineer vs. manager response shaping)
  rather than leaving it as a production note.
- Add an evaluation harness with a fixed set of test prompts and expected-behaviour
  assertions (stretch goal from the assessment brief).
