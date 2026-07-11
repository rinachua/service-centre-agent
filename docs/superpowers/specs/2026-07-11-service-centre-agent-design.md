# Semiconductor Equipment Service Centre — Agentic AI Assistant

Design spec. Written for the "Agentic AI Software Architect" interview assessment.

## 1. Goal

A conversational assistant that helps equipment engineers and service managers prioritise
open tickets, investigate root causes, and generate structured follow-up records — by
reasoning across purpose-built backend services rather than touching data directly.

Four example queries define the acceptance bar:

1. "Which open equipment tickets should I prioritise today and why?"
2. "For tool ETCH-07, summarise the recent alarm history and likely causes."
3. "Compare this issue against similar historical cases and suggest next troubleshooting steps."
4. "Generate a structured service follow-up note for the engineer."
5. "Show me the evidence behind your recommendation."

## 2. Assumptions

- Single-tenant demo. No multi-fab / multi-org concerns.
- Synthetic data only; no proprietary or confidential content.
- User is already authenticated upstream (e.g. by an API gateway); this system does not
  implement login. See §8 for the access-control assumption in detail.
- A real Claude API key is available and used for the LLM component (`ANTHROPIC_API_KEY`
  env var). No mock LLM mode is implemented, per user decision.
- "Microservices" run as separate Docker Compose containers, each independently
  runnable and testable.
- Reasoning depth targets the assessment's "minimum functional scope, polished" bar —
  stretch goals (RBAC enforcement, event-driven ingestion, human-in-the-loop *approval
  workflow*, dashboard, eval harness) are documented as future work, not built, with one
  partial exception noted in §6.4 (write actions are already human-gated by construction).

## 3. Architecture overview

```
                         ┌─────────────────────────┐
                         │   Chat UI (static JS)    │
                         │   served by orchestrator │
                         └────────────┬─────────────┘
                                      │ POST /chat
                                      ▼
                         ┌─────────────────────────┐
                         │   agent-orchestrator     │◄──── audit_log (SQLite)
                         │   (FastAPI + Claude      │
                         │    tool-use loop)        │
                         └───┬───────┬───────┬──────┘
                 REST        │       │       │        REST
        ┌────────────────────┘       │       └────────────────────┐
        ▼                            ▼                            ▼
┌───────────────┐         ┌────────────────────┐         ┌──────────────────┐
│ ticket-service │         │ equipment-history-  │         │ knowledge-service │
│ (SQLite)       │         │ service (SQLite)    │         │ (TF-IDF search)   │
└───────────────┘         └────────────────────┘         └──────────────────┘
        ▲
        │ REST
        │
┌───────────────────┐
│ recommendation-    │
│ service (rule-based│
│ scoring, no LLM)   │
└───────────────────┘
```

All inter-service calls are synchronous REST (`httpx`) over the Compose network. The
orchestrator is the only service that talks to Claude and the only service the UI talks
to — other services are never called directly by the client, and never call each other.

### 3.1 Why this shape (vs. alternatives considered)

| Approach | Description | Pros | Cons | Decision |
|---|---|---|---|---|
| **Live Claude tool-use loop** (chosen) | Claude sees each tool result and decides the next call itself, over REST calls to each service. | Matches the brief's suggested "FastAPI + tool-calling" pattern; most genuinely agentic option — plan can change mid-investigation (e.g. escalate to knowledge-service only if history looks inconclusive); naturally supports "show me the evidence" since every tool call is already logged. | More LLM round-trips per query (3-6 calls) → higher cost and latency than a single-shot approach. | **Chosen.** |
| **Plan-then-execute** | One Claude call produces a JSON plan, a deterministic router executes it, a second Claude call synthesises the answer. | Cheaper — fewer LLM round-trips; the deterministic routing step is easy to unit test. | Can't adapt mid-investigation once the plan is set; weaker demonstration of agentic reasoning for this assessment. | Rejected for the demo; noted as a cost-optimisation path for production (§9). |
| **Event-driven (queue) inter-service calls** | Orchestrator publishes tool-call requests to a broker (e.g. Redis/RabbitMQ); services subscribe and respond async. | Matches one of the brief's allowed interface styles; demonstrates async/event-driven patterns. | Adds broker infrastructure and response-correlation complexity disproportionate to a lightweight demo; brief explicitly says REST is acceptable. | Rejected. |

**Deterministic vs. LLM boundary.** `recommendation-service` does priority *scoring*
(a weighted formula: severity, downtime impact, recurrence, ticket age) — deterministic,
auditable, and cheap, because ranking a fixed set of tickets by known fields doesn't
benefit from an LLM and benefits a lot from being reproducible. Root-cause *hypotheses*
are generated by Claude, because that requires synthesising unstructured evidence
(alarm patterns, SOP text, shift notes) in a way a fixed formula can't. This split is
called out explicitly because the assessment asks for it.

## 4. Services

### 4.1 ticket-service (port 8001)

Owns open/closed service tickets and generated follow-up notes.

| Endpoint | Purpose |
|---|---|
| `GET /tickets?status=open&tool_id=` | List tickets, filterable |
| `GET /tickets/{ticket_id}` | Single ticket detail |
| `POST /tickets/{ticket_id}/followups` | Persist a structured follow-up note (human-triggered, see §6.4) |
| `GET /tickets/{ticket_id}/followups` | List follow-up notes for a ticket |

Fields: `ticket_id, tool_id, line, process_area, title, description, severity
(critical/high/medium/low), status (open/in_progress/closed), downtime_impact_hours,
reported_by, created_at`.

### 4.2 equipment-history-service (port 8002)

Owns equipment assets and their alarm/maintenance history.

| Endpoint | Purpose |
|---|---|
| `GET /assets` / `GET /assets/{tool_id}` | Asset status: line, process area, current status, recent downtime |
| `GET /assets/{tool_id}/history` | Alarm + maintenance records for a tool, newest first |
| `GET /history/search?q=` | Keyword search across all history (alarm code, description) |

History fields: `record_id, tool_id, event_type (alarm/maintenance), code, description,
date, resolution, parts_replaced`.

### 4.3 knowledge-service (port 8003)

Retrieval over unstructured documents: troubleshooting guides, SOP excerpts, shift
handover notes. Implemented with TF-IDF + cosine similarity (`scikit-learn`) — no
embedding API calls, no vector DB, deterministic and free to run. Realistic for a
3-document corpus; documented in §9 as the first thing to swap for a real vector store
at production scale.

| Endpoint | Purpose |
|---|---|
| `GET /search?q=&top_k=5` | Ranked snippets with `doc_id, title, excerpt, score` |
| `GET /documents/{doc_id}` | Full document text |

### 4.4 recommendation-service (port 8004)

Deterministic, rule-based. No LLM calls.

| Endpoint | Purpose |
|---|---|
| `POST /priority-score` | Given a list of ticket IDs (or "all open"), return ranked list with per-factor score breakdown |

Scoring formula (weights configurable via env, defaults shown):
`score = 0.4*severity_weight + 0.3*downtime_hours_normalised + 0.2*recurrence_count + 0.1*age_days_normalised`.
Recurrence count is derived by matching open tickets against equipment-history-service
records for the same `tool_id` and similar `code`/description (simple substring/keyword
match, not ML).

### 4.5 agent-orchestrator (port 8000)

The only service exposed to the user. Responsibilities:

- Serves the static chat UI (`GET /`).
- `POST /chat` — conversational endpoint, runs the tool-use loop, returns the structured
  answer (§6).
- `GET /audit/{request_id}` — replay the full tool-call trace for a prior request.
- Holds the Claude client, tool schema (mapped 1:1 to the REST endpoints above), system
  prompt, grounding checks, retry/fallback logic, and the `audit_log` SQLite table.

## 5. Data model / synthetic dataset

Fab-flavored synthetic data, seeded via `scripts/seed_data.py` per service on startup:

- **5 equipment assets**: ETCH-07, LITHO-03, CMP-02, DEP-05, CLEAN-11, spread across
  2 lines (Line-A, Line-B) and process areas (Etch, Litho, CMP, Deposition, Wet Clean).
- **12 tickets**, mixed severity/status, referencing the 5 tools, with realistic
  downtime figures.
- **12 alarm/maintenance history records**, several deliberately clustered on ETCH-07
  (repeated RF generator alarms) to give the "compare against similar historical cases"
  query something real to find.
- **3 documents**: an etch-chamber troubleshooting guide, an RF-generator preventive
  maintenance SOP excerpt, and a shift handover note mentioning an observation relevant
  to one of the open tickets — so a query can plausibly need all three sources.

## 6. Agent workflow

### 6.1 Planning & tool use

System prompt establishes role, grounding rules ("only state facts backed by tool
results; cite record IDs for every claim; if evidence is missing or conflicting, say so
rather than guessing"), and the tool list. Claude receives the user query, decides which
tool(s) to call, receives results, and iterates — up to 6 tool-call rounds — before
producing a final answer. This lets it, for example, pull open tickets, then pull
history only for the highest-severity tool, then search knowledge only if history alone
doesn't explain the pattern.

### 6.2 Response synthesis

Final answer must validate against a fixed Pydantic schema:

```
recommendation: str
evidence: list[{source: str, record_id: str, detail: str}]
assumptions: list[str]
confidence: "low" | "medium" | "high"
next_action: str
```

If Claude's output doesn't validate, the orchestrator sends one repair prompt quoting
the validation error. If that also fails, it falls back to a templated answer built
directly from the raw tool results already collected in that session (never a bare
error to the user).

### 6.3 Evidence grounding (hallucination control)

Every `record_id` in the final answer is checked against the set of IDs actually
returned by tool calls during that session. Any unverifiable ID is flagged
`"unverified"` in the response rather than presented as fact, and logged as a warning
in the audit trail. This is a lightweight, mechanical check — not a substitute for
prompt-level grounding instructions, but a backstop against them failing.

Tool results (especially knowledge-service snippets, which contain free-text shift
notes) are treated as untrusted data: the system prompt explicitly instructs Claude to
treat tool output as information, not instructions, and the orchestrator scans
retrieved text for common prompt-injection phrasing (e.g. "ignore previous
instructions") and logs a warning if found. Documented as a basic mitigation, not a
comprehensive defence.

### 6.4 Write actions are human-gated

The agent can only *draft* a follow-up note (returned in the structured response). It
never calls `POST /tickets/{id}/followups` itself. Persisting the note is a distinct,
explicit action in the UI ("Save follow-up"), triggered by the user. This satisfies the
spirit of the "human-in-the-loop approval before creating or updating records" stretch
goal without adding an approval-workflow subsystem — the gate is structural, not
procedural.

### 6.5 Failure handling

- Each downstream REST call: 3s timeout, 1 retry with backoff.
- On repeated failure, the orchestrator injects a synthetic tool result noting the
  service was unavailable and continues the loop; the final answer's `assumptions`
  field records the gap explicitly (e.g. "equipment-history-service was unreachable;
  recommendation is based on ticket data only").
- The orchestrator never lets a downstream failure become an unhandled 500 to the user.

## 7. Observability

- Structured JSON logs to stdout from every service (request id, method, path, latency,
  status) — visible via `docker compose logs`.
- `agent-orchestrator` additionally writes one `audit_log` row per request: user query,
  ordered list of tool calls (name, args, result summary, latency), grounding-check
  outcome, final answer, total latency. Retrievable via `GET /audit/{request_id}`, which
  is what answers "show me the evidence behind your recommendation" at the trace level
  (the structured `evidence` field in the chat response answers it at the
  answer level).
- A `request_id` (UUID) is generated per `/chat` call and passed as a header on every
  downstream REST call, so log lines across services can be correlated by grepping one
  ID — a lightweight stand-in for distributed tracing.

## 8. Security / access-control assumption

No authentication or authorization is implemented in this demo. Documented assumption:
in production, the orchestrator would sit behind an API gateway performing OIDC-based
authentication, and service-to-service calls would carry a short-lived JWT or use mTLS,
with the orchestrator enforcing role checks (engineer vs. manager) before including
certain fields (e.g. cost/downtime rollups) in a response. This is called out as future
work rather than implemented, to keep the demo's scope aligned with "minimum functional
scope, polished."

## 9. Trade-offs & future production considerations

- **LLM cost/latency**: a live tool-use loop makes 3-6 Claude calls per query. At
  production volume, a plan-then-execute pattern (§3.1) or caching repeated
  sub-queries (e.g. "get open tickets") would reduce cost.
- **Knowledge retrieval**: TF-IDF is adequate for 3 documents; a real deployment with
  hundreds of SOPs would need a proper vector store and chunking strategy.
- **Recurrence detection**: currently keyword/substring matching in
  recommendation-service; a production version would want a proper similarity model.
- **Audit storage**: SQLite is fine for a demo; production would want an append-only
  store (e.g. a dedicated audit table in Postgres, or a log pipeline) with retention
  policy.
- **Resilience**: no circuit breaker; at this scale a simple retry is sufficient, but a
  service with sustained failures should trip a breaker rather than retry indefinitely.

## 10. Repository structure

```
service-centre-agent/
  docker-compose.yml
  .env.example
  README.md
  docs/
    architecture.md              (expanded version of this spec + diagrams, for submission)
    superpowers/specs/           (this file)
  services/
    ticket-service/
    equipment-history-service/
    knowledge-service/
    recommendation-service/
    agent-orchestrator/
      static/                    (chat UI)
  data/
    seed/                        (synthetic JSON source data)
  scripts/
    seed_data.py
  tests/
    ticket-service/
    equipment-history-service/
    knowledge-service/
    recommendation-service/
    agent-orchestrator/          (includes mocked tool-loop integration test)
```

## 11. Testing strategy

- `recommendation-service`: unit tests on the scoring formula (pure functions, no I/O).
- `knowledge-service`: unit tests on search ranking for known queries.
- `agent-orchestrator`: unit tests on the Pydantic output schema validation and the
  ID-grounding check; one integration test that runs the tool-use loop against the four
  downstream services mocked with `respx`, so CI never calls the real Anthropic API.
- Each REST service: a handful of endpoint tests via `httpx`'s `ASGITransport` against
  an in-memory/temp SQLite DB.

## 12. Deliverables mapping

| Brief requirement | Where it's satisfied |
|---|---|
| Conversational interface / API | `POST /chat` + static chat UI |
| Orchestration/agent service | `agent-orchestrator` |
| ≥2 backend services | 4: ticket, equipment-history, knowledge, recommendation |
| Structured final answer w/ evidence, assumptions, next action | §6.2 schema |
| Local runnable via Docker Compose | `docker-compose.yml` |
| ≥10 tickets, ≥5 assets, ≥10 history records, ≥3 documents | §5 |
| Observability | §7 |
| Safety/reliability | §6.3, §6.5, §8 |
| README + architecture doc | `README.md`, `docs/architecture.md` |
