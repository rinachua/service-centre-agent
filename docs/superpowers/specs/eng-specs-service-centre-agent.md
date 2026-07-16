# Semiconductor Equipment Service Centre — Agentic AI Assistant

Engineering spec for Semiconductor Equipment Service Centre — Agentic AI Assistant. 

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
- A real Claude API key is expected and used for the LLM component when available
  (`ANTHROPIC_API_KEY` env var). If no key is set, the system automatically falls back to
  a deterministic, rule-based offline responder — see §6.6 — rather than failing; this
  satisfies the assessment brief's §8 constraint for a documented local/mock substitute.
- "Microservices" run as separate Docker Compose containers, each independently
  runnable and testable.
- Reasoning depth targets the assessment's "minimum functional scope, polished" bar.
  Stretch goals status: human-in-the-loop write approval is satisfied by construction,
  not a separate build (§6.4); structured output schema validation is hardened, not
  just present (`AgentAnswer`'s `model_validator` in `app/schemas.py`, and the audit
  log's `schema_validation_failures` column); an evaluation harness
  (`tests/test_evaluation_harness.py`) and a dashboard (`GET /dashboard/*`,
  `static/dashboard.html`) are both built; RBAC is built as scoped, unenforced
  *assumptions*, not real access control — real RBAC enforcement remains deferred
  future work (both §8). Event-driven ticket ingestion / scheduled refresh is the one stretch goal
  deliberately not built, with reasoning in §9's trade-offs list.

## 3. Architecture overview

```
                         ┌──────────────────────────┐
                         │   Chat UI (static JS)    │
                         │   served by orchestrator │
                         └────────────┬─────────────┘
                                      │ POST /chat
                                      ▼
                         ┌──────────────────────────┐
                         │   agent-orchestrator     │◄──── audit_log (SQLite)
                         │   (FastAPI + bounded     │
                         │   plan→execute→synth)    │
                         └──┬──────┬──────┬──────┬──┘
                REST        │      │      │      │      REST
        ┌───────────────────┘      │      │      └───────────────────────────────┐
        │                  ┌───────┘      └────────────┐                         │
        ▼                  ▼                           ▼                         ▼
┌────────────────┐  ┌─────────────────────┐     ┌───────────────────┐    ┌──────────────────────┐
│ ticket-service │  │ equipment-history-  │     │ knowledge-service │    │ recommendation-      │
│ (SQLite)       │  │ service (SQLite)    │     │ (TF-IDF search)   │    │ service (rule-based) │
└────────────────┘  └─────────────────────┘     └───────────────────┘    └──────────────────────┘
                                                                   
```

All inter-service calls are synchronous REST (`httpx`) over the Compose network. The
orchestrator is the only service that talks to Claude and the only service the UI talks
to — other services are never called directly by the client, and never call each other.
All four downstream services, including `recommendation-service`, are called directly
by `agent-orchestrator`; none of them are called by another backend service. In
particular, `score_priority` (§4.5's `ToolExecutor._tool_score_priority`) is a compound
tool the orchestrator itself runs in three sequential REST calls — `GET /tickets` on
ticket-service, then `GET /assets/{tool_id}/history` on equipment-history-service for
each affected tool, then `POST /priority-score` on recommendation-service with both
results — not a chain where one backend service calls another.
`recommendation-service`'s own code makes no outbound HTTP calls at all; it is a pure
function over whatever the orchestrator hands it.

**Agentic AI components, named explicitly.** This is an agentic AI system, not a chatbot
wrapped around an LLM: `agent-orchestrator` is the agent, and it decomposes into the
standard planner/act/reason/verify roles, each mapped to a concrete piece of code below
rather than left implicit.

| Agentic role | What plays that role                                                                                                                                                                                              | Where it lives |
|---|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---|
| **Perception (input)** | The static chat UI's `POST /chat` request — a natural-language user query, no structure required.                                                                                                                 | `agent-orchestrator` `/chat` endpoint (§6.4) |
| **Planner** | One Claude call - using Haiku model (the cheaper model) that reads the query and the tool schema, and decides which tool(s) to invoke — never sees results before deciding, never free-texts.                     | `run_agent_loop`'s plan phase (§6.1) |
| **Tools / actions** | The only way the agent touches data — never direct DB access. Four distinct capabilities: read tickets, read equipment/alarm history, retrieve knowledge-base text (RAG), and get a deterministic priority score. | `ticket-service`, `equipment-history-service`, `knowledge-service`, `recommendation-service` (§4) |
| **Executor** | Deterministic router that turns the planner's tool requests into real REST calls, with timeout/retry/error-containment — no LLM involved in this step.                                                            | `ToolExecutor` (§4.5) |
| **Reasoner / synthesiser** | One Claude call (full model) that turns raw tool results into the structured final answer — recommendation, evidence, assumptions, confidence, next action.                                                       | `run_agent_loop`'s synthesise phase (§6.1, §6.2) |
| **Self-critique / recovery** | The synthesiser judges its own evidence and can request exactly one more planner→executor→synthesiser round if insufficient — capped, not open-ended.                                                             | The "optional single revision" step (§6.1) |
| **Verifier (grounding)** | Checks every cited `record_id` in the final answer against IDs actually returned by tool calls that session; flags anything unverifiable rather than trusting it.                                                 | `verify_evidence` / `extract_known_ids` (§6.3) |
| **Memory (per-request, not conversational)** | Persists the full tool-call trace, injection flags, and final answer for later audit/replay. Each `/chat` call is independent — there is no multi-turn conversation memory across requests.                       | `audit_log` SQLite table, `GET /audit/{request_id}` (§7) |
| **Action gate (human-in-the-loop)** | The agent only ever *drafts* a follow-up note; nothing is written to `ticket-service` without an explicit, separate human action.                                                                                 | UI "Save follow-up" button → `POST /tickets/{ticket_id}/followups` (§6.4) |
| **Fallback planner/reasoner** | Deterministic, rule-based stand-in for the planner and synthesiser roles above when no LLM is available — same roles, same flow, different implementation.                                                        | `OfflineResponder` (§6.6) |

### 3.1 Choosing the orchestration pattern

| Approach                                                                    | Description                                                                                                                                                                                                                                                                                                                        | Pros | Cons | Decision |
|-----------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---|---|---|
| **✅ Bounded hybrid: plan → execute → synthesise, with one capped revision** | One Claude call — using Haiku model (the cheaper model) turns the query into a tool-call plan; a deterministic router executes it; one Claude call — using Sonnet model (the stronger model) synthesises the answer and flags whether evidence was sufficient. If not, exactly one more execute→synthesise round runs — never more. | Fixed worst-case cost ceiling (2 calls typical, 3 max, vs. an open-ended 3-6) and therefore predictable at procurement/budget-planning time; the "plan" is a discrete, loggable artifact — stronger auditability story than an autonomous loop deciding its own next step; still recovers from a wrong first plan once, unlike plain plan-then-execute; cheap model on the planning call, full model only where reasoning quality matters (model tiering). | Less adaptive than a fully open loop in the rare case where more than one revision would genuinely help; the plan/synthesis split adds a small amount of orchestration complexity (a JSON contract between the two phases) that a single continuous loop doesn't need. | **Chosen.** Revised from an initial live tool-use loop after weighing cost predictability and auditability for a cost-sensitive/regulated deployment context — see §9.1. |
| **Live Claude tool-use loop**                                               | Claude sees each tool result and decides the next call itself, over REST calls to each service, for up to 6 rounds.                                                                                                                                                                                                                | Most genuinely agentic option — the plan can change mid-investigation on every single tool result, not just once; naturally supports "show me the evidence" since every tool call is already logged. | More LLM round-trips per query (3-6 calls, variable and hard to predict) → higher and less predictable cost/latency. At Claude Sonnet 5 pricing (introductory, through Aug 2026): roughly **$0.017 for a simple query, $0.027 typical, up to ~$0.05 for a heavy multi-tool investigation** — see §9.1. An autonomous loop is also a weaker audit story than a discrete, reviewable plan. **Expensive to scale to production**: cost grows per-query with how many rounds each one needs, not a fixed ceiling, so spend scales unpredictably with query volume and complexity rather than linearly. | Considered first, superseded by the bounded hybrid above once cost predictability and auditability were weighted alongside "most agentic" — see §9.1. |
| **Plain plan-then-execute (no revision)**                                   | One Claude call produces a JSON plan, a deterministic router executes it, a second Claude call synthesises the answer — always exactly 2 calls, no recovery path.                                                                                                                                                                  | Cheapest and most predictable of all three — fixed 2 calls, always. | Can't adapt at all if the first plan turns out wrong; the bounded hybrid gets almost all of this pattern's cost benefit while keeping a capped recovery path, so this variant's extra restriction wasn't worth it. | Rejected — the bounded hybrid dominates it. |
| **Event-driven (queue) inter-service calls**                                | Orchestrator publishes tool-call requests to a broker (e.g. Redis/RabbitMQ); services subscribe and respond async.                                                                                                                                                                                                                 | Matches one of the brief's allowed interface styles; demonstrates async/event-driven patterns. | Adds broker infrastructure and response-correlation complexity disproportionate to a lightweight demo; brief explicitly says REST is acceptable. | Rejected. |

(This comparison is about call *pattern* — how many Claude calls, in what shape — and
holds regardless of which client answers those calls; what happens when no
`ANTHROPIC_API_KEY` is available at all is a separate, orthogonal decision documented
in §6.6.)

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

The browser never calls `POST /tickets/{ticket_id}/followups` here directly — it goes
through `agent-orchestrator`'s identically-shaped proxy endpoint (§4.5), same as the
dashboard data endpoints, since ticket-service has no CORS configuration.

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

Retrieves from unstructured documents — troubleshooting guides, SOP excerpts, shift handover notes — using pure-Python TF-IDF and cosine similarity (no `scikit-learn`). No embedding API calls, no vector DB: deterministic and free to run, which is realistic at this 3-document scale. §9 documents this as the first thing to swap for a real vector store once the corpus grows.

| Endpoint | Purpose |
|---|---|
| `GET /search?q=&top_k=5` | Ranked snippets with `doc_id, title, excerpt, score` |
| `GET /documents/{doc_id}` | Full document text |

### 4.4 recommendation-service (port 8004)

Deterministic, rule-based. No LLM calls.

| Endpoint | Purpose |
|---|---|
| `POST /priority-score` | Given a list of ticket IDs (or "all open"), return ranked list with per-factor score breakdown |

Scoring formula (weights are configurable, defaults shown):
`score = 0.4*severity_weight + 0.3*downtime_hours_normalised + 0.2*recurrence_count + 0.1*age_days_normalised`.
Each term is normalised to a 0–1 range before being weighted: `severity_weight` comes
from a fixed lookup (critical=1.0, high=0.75, medium=0.5, low=0.25); `downtime_hours_normalised`
and `age_days_normalised` are the ticket's downtime and age divided by the maximum seen
in the current batch of tickets being ranked, capped at 1.0; `recurrence_count` is the
raw match count below, divided by 5 and capped at 1.0. The weights themselves — severity > downtime > recurrence > age, in round numbers summing to 1.0 — are a hand-picked
prioritisation ordering, not derived from any calibration process or historical outcome
data (§9.2 explains why that data doesn't exist yet). Recurrence count is derived by
matching open tickets against equipment-history-service records for the same `tool_id`
and similar `code`/description (simple substring/keyword match, not ML) — the matching
itself runs inside `recommendation-service` against history data the orchestrator
already fetched and included in the `/priority-score` request body (see §3's note on
`score_priority`); `recommendation-service` never queries equipment-history-service
directly.

**Deterministic vs. LLM boundary.** This scoring formula is deliberately deterministic,
auditable, and cheap: ranking a fixed set of tickets by known fields doesn't benefit
from an LLM, and benefits a lot from being reproducible. Root-cause *hypotheses*, by
contrast, are generated by Claude, since synthesising unstructured evidence — alarm
patterns, SOP text, shift notes — into a plausible explanation is exactly what a fixed
formula can't do.

### 4.5 agent-orchestrator (port 8000)

The only service exposed to the user. Responsibilities:

- Serves the static chat UI (`GET /`) and the dashboard (`GET /dashboard.html`).
- `POST /chat` — conversational endpoint, runs the bounded plan→execute→synthesise flow
  (§6.1), returns the structured answer (§6.2). Accepts an optional `X-User-Role` header
  (§8).
- `GET /audit/{request_id}` — replay the full tool-call trace for a prior request.
  `GET /audit?limit=` — summary rows for the most recent requests (dashboard list view).
- `GET /dashboard/tickets`, `GET /dashboard/assets`, `GET /dashboard/priority` —
  same-origin data endpoints backing the dashboard. The browser only ever talks to
  `agent-orchestrator`; these proxy/reuse server-to-server calls to the other 4
  services so none of them need CORS configuration (they're never called by a
  client directly, only server-to-server — see §3's inter-service diagram).
- `POST /tickets/{ticket_id}/followups` — same proxy pattern as the dashboard
  endpoints above: the "Save follow-up" button (§6.4) calls this same-origin route
  on `agent-orchestrator`, which then makes the real server-to-server call to
  ticket-service's identically-shaped endpoint (§4.1). The browser never calls
  ticket-service directly, for the same CORS reason as the dashboard data.
- Holds the Claude client (planner model + synthesis model; automatically substituted
  with the offline `OfflineResponder` fallback when `ANTHROPIC_API_KEY` is unset — §6.6),
  tool schema (mapped 1:1 to the REST endpoints above), system prompts, grounding checks,
  retry/fallback logic, and the `audit_log` SQLite table.

## 5. Data model / synthetic dataset

**Synthetic data**

Synthetic data modeled on a semiconductor fab, stored as static JSON files under
`data/seed/` and loaded by each service on startup:

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

**How seeding interacts with SQLite** 

Each of ticket-service and equipment-history-service
manages its own SQLite database file. The first time it starts up, it creates that file
and checks whether its main table already has any rows in it. If the table is empty, it
reads the matching JSON file from `data/seed/` and inserts every record from it as a new
row — that's the actual seeding step. If the table already has rows, it skips this
entirely and leaves the existing data untouched.

That skip-if-not-empty check is what it relies on, and it only works because the seed
files and the live database live in two genuinely separate places. Docker Compose gives
each container read-only access to the `data/` folder holding the seed JSON, while the
actual SQLite database file lives in its own separate storage area that Docker keeps
around even after the container stops or gets rebuilt. So seeding really only ever
happens once per database, at the moment it's created empty — after that, restarting
the service never re-inserts or overwrites anything, even if the data has since changed,
for example by saving a follow-up note through the UI. To reset back to the original
synthetic dataset, you have to explicitly clear that stored data with
`docker compose down -v`, which forces a fresh, empty database — and therefore a fresh
seed — the next time you run `docker compose up`.

The other two services don't follow this pattern. knowledge-service reads the same seed
documents into an in-memory TF-IDF index at startup — no SQLite involved, since it has
no mutable state to persist. recommendation-service consumes no seed data at all; it's
a pure function with no storage of its own.

## 6. Agent workflow

### 6.1 Planning & tool use (bounded hybrid: plan → execute → synthesise, ≤1 revision)

Three phases, at most 3 Claude calls total (2 in the common case). Every "Claude call"
below is a real, live call to the Anthropic API whenever `ANTHROPIC_API_KEY` is set —
that's the default, full-fledged mode. When no key is present, `app/main.py` automatically
substitutes the deterministic `OfflineResponder` (§6.6) in place of the real client instead
— same steps, same call count, same JSON contracts, only the source of the plan/synthesis
answers changes. The rest of this section describes the live-mode behaviour; §6.6 covers
exactly how the fallback answers the same two call sites.

1. **Plan** (cheap model, e.g. Haiku, or the offline fallback's keyword heuristics — §6.6
   — if no key is set). This call sends Claude the system prompt, the list of available
   tools (§4.5), and the user's query, with `tool_choice: "any"` so the response is one
   or more tool calls, never free text — and nothing is executed yet, since Claude only
   names its choice; the real call happens afterward, made by the orchestrator's own
   code. Claude may request multiple tool calls in this single
   response (e.g. `get_tickets` and `get_equipment` together); the orchestrator treats
   the full set of returned tool_use blocks as "the plan."
2. **Execute** (deterministic, no LLM either way). The orchestrator's router runs
   exactly the planned tool calls against the 4 backend services via the same
   `ToolExecutor` (§4.5) used previously, with the same per-call timeout/retry/error-
   containment behaviour (§6.5) — a `ServiceError` here never crashes the request.
3. **Synthesise** (full model, e.g. Sonnet, or the offline fallback's templated
   answer-builder — §6.6 — if no key is set). User query + all tool results are sent to
   Claude, which returns a JSON object containing a best-effort `answer` (§6.2's
   schema), plus `sufficient: bool` and, if `false`, one `additional_tool_request`
   naming exactly one more tool call it needs.
4. **Optional single revision (live mode only).** If `sufficient` was `false` and no
   revision has run yet for this request, the orchestrator executes that one additional
   tool call (step 2 again, deterministic) and calls Claude once more to synthesise
   (step 3 again) — but this final round has no `sufficient`/`additional_tool_request`
   fields in its expected output, so no further revision can be requested. Worst case:
   plan (1) + synthesise (1) + revise-synthesise (1) = 3 Claude calls, never more. The
   offline fallback always reports `sufficient: true`, so this step never fires when
   there's no API key — a deliberate scope reduction (§6.6) since the revision path is
   already fully covered by `test_loop.py`'s unit tests against the real contract.
   `AgentTrace.revised` records in-process whether this path fired for a given
   request (useful for debugging and as a hook for future audit-log extension), but
   it is not currently persisted to the `audit_log` table by the `/chat` handler — the
   audit trail (§7) does not yet show, after the fact, whether a given past request
   took the revision path.

This recovers most of a fully open loop's value (a wrong first plan can still be
corrected once, e.g. escalate to knowledge-service if history alone didn't explain the
pattern) while keeping a hard, predictable cost ceiling — the deciding factor for the
cost-sensitive/regulated deployment context this was revised for (§9.1).

### 6.2 Response synthesis

The final answer must validate against a fixed Pydantic schema (`AgentAnswer`):

```
recommendation: str
evidence: list[{source: str, record_id: str, detail: str}]
assumptions: list[str]
confidence: "low" | "medium" | "high"
next_action: str
followup_note: {ticket_id: str, summary: str, root_cause: str, next_action: str} | None
```

`sufficient: bool` and `additional_tool_request` are not part of this schema — they're
separate, top-level fields that wrap around it, and only in the first synthesis call's
raw JSON:

```json
{
  "answer": { ...AgentAnswer fields above... },
  "sufficient": true | false,
  "additional_tool_request": {"tool_name": string, "input": object} | null
}
```

The orchestrator reads `sufficient`/`additional_tool_request` from this wrapper to
decide whether to run the one allowed revision round (§6.1); they are never exposed to
the client — the `/chat` response only ever contains the `answer` shape above.

These two fields don't show up at all in the revision call, if it fires. That call's
raw output is unwrapped `AgentAnswer` directly — no `"answer"` key, and no
`sufficient`/`additional_tool_request` fields in its expected shape at all. That's also
*why* a second revision is structurally impossible, not just a rule Claude is asked to
follow: there's nowhere in the revision response's schema to even request one.

If Claude's output doesn't validate against the expected schema at either synthesis
step, the orchestrator falls back immediately to a templated answer built directly
from the raw tool results already collected — there is deliberately no repair
round-trip in this design, because a repair
call would reintroduce the unbounded-cost problem the bounded hybrid exists to remove.
Claude's structured-output reliability is high enough that this is an acceptable
trade: a malformed response is rare, and the fallback path already guarantees the user
never sees a bare error.

### 6.3 Evidence grounding (hallucination control) and prompt-injection scanning

**Evidence grounding (hallucination control)** 

Every tool call made during a request
returns real records — tickets, history entries, documents, assets — each carrying an
ID field (`ticket_id`, `record_id`, `tool_id`, `doc_id`, or `followup_id`). The
orchestrator collects every one of these IDs into a single known-IDs set for that
session. Before the final answer is returned, each `evidence[].record_id` Claude cites
is checked against that set: if the ID genuinely appears in data fetched this session,
it's marked `verified: true`; if Claude cites an ID that was never actually returned by
any tool call — for instance, inventing a plausible-looking ticket number, or citing one
it half-remembers from training data rather than from this session's real results — it's
marked `verified: false` instead of being silently presented as fact. This is a
mechanical, structural check, not a judgment call: it doesn't know whether a claim is
*true*, only whether it's *traceable* to real data from this session. That's a
deliberately narrow goal — it catches fabricated citations, not subtler reasoning
errors — and it exists as a backstop for when prompt-level grounding instructions fail,
not a replacement for them.

The outcome of this check isn't written to a separate log line; it's recorded as
structured data on the answer itself — the `verified` flag on each evidence item — and
that full answer, verified flags included, is what gets persisted to the audit trail
(§7). There's no separate grounding-outcome record to check elsewhere.

**Prompt-injection scanning** 

Tool results —
especially knowledge-service snippets, which contain free-text shift notes — are
treated as untrusted data: the system prompt explicitly instructs Claude to treat tool
output as information, not instructions. Separately, before any planned tool call
executes (in both the initial plan and the one optional revision round), the
orchestrator scans that call's *input* — the arguments Claude requests a tool be called
with, not the data the tool returns — for common prompt-injection phrasing (e.g.
"ignore previous instructions"). Any match is recorded and surfaces to the caller as an
assumption on the final answer, the same way evidence verification surfaces through the
answer rather than a separate log. This covers tool *inputs* only — tool *results* are
not separately scanned; the only mitigation for injected content reaching the synthesis
model via a retrieved result is the system-prompt instruction above, not a scanner.
This is a basic mitigation, not a comprehensive defence — result-side scanning remains
documented future work, not yet built.

### 6.4 Write actions are human-triggered by design

The agent is deliberately restricted to *drafting* a follow-up note — it never calls
`POST /tickets/{id}/followups` itself, and has no code path that could. Persisting a
note requires a distinct, explicit action in the UI ("Save follow-up"): nothing is
written to `ticket-service` unless a human chooses to write it. This directly satisfies
the "human-in-the-loop approval before creating or updating records" stretch goal, and
does so with less complexity than a typical approval-workflow subsystem — queued
writes, approve/reject states, an audit of who approved what — would require: rather
than building a gate for the agent to pass through, the design simply never gives the
agent the ability to write in the first place. The safety property holds by
construction, not by policy, which means there's no approval step to misconfigure,
bypass, or forget to check.

### 6.5 Failure handling

- Each downstream REST call: 3s timeout, 1 retry on connection-level failure only — an
  HTTP error status (4xx/5xx) is not retried, since it won't change on a second
  attempt. Implemented once, as `request_with_retry()` (`app/tools.py`), and shared by
  every downstream call in the service: `ToolExecutor`'s tool calls, and the
  dashboard/follow-up-save endpoints (§4.5), which previously made one-shot calls with
  no retry at all — the one inconsistency a prior review of this codebase caught and
  closed, with a regression test proving a single transient failure now recovers
  instead of surfacing a 502.
- On repeated failure, the orchestrator records a synthetic "unavailable" result for
  that tool call and proceeds to the synthesis step anyway; the final answer's
  `assumptions` field records the gap explicitly (e.g. "equipment-history-service was
  unreachable; recommendation is based on ticket data only").
- The orchestrator never lets a downstream failure become an unhandled 500 to the user.
- **`ToolExecutor.execute()` dispatches through a `_handlers` registry — a
  `{tool_name: function}` map — rather than a growing `if/elif` chain.** `TOOL_DEFS`
  (§4.5) is the tool schema Claude sees; `_handlers` is a separate, internal lookup
  mapping each tool name to the Python method that actually runs it. This keeps
  `execute()` itself closed to modification: adding a future tool #8 means adding one
  entry to `TOOL_DEFS` and one to `_handlers`, not editing a conditional that every
  prior tool's branch already lives inside. Not adopted because 7 tools demands it — it
  wouldn't — but because the retry-logic work above was already touching this class,
  and the registry pattern cost nothing extra to apply while there.

### 6.6 LLM-unavailable fallback (brief §8 compliance)

The brief's constraints (§8) require that the solution "use a real LLM API or a clearly
documented local/mock substitute if API access is not available," and that "any mock
should preserve the intended architecture and tool-calling flow." This section documents
that substitute.

**Summary**

| Condition | LLM client used | Behaviour |
|---|---|---|
| `ANTHROPIC_API_KEY` is set and valid | `anthropic.Anthropic(...)` (real Claude API) | Full-fledged: real Claude Haiku plan call, real Claude Sonnet synthesis, exactly as described in §6.1. |
| `ANTHROPIC_API_KEY` is unset/empty | `OfflineResponder` (`app/offline_responder.py`) | Automatic fallback, no flag required: keyword-heuristic tool planning, templated synthesis built from real tool results. Same plan→execute→synthesise flow, same `AgentAnswer` shape, same grounding/audit/injection-scanning. Every offline answer carries `confidence: "low"` and an explicit assumption disclosing it wasn't generated by live Claude. |
| `ANTHROPIC_API_KEY` is set but invalid (revoked, malformed, wrong permissions) | `anthropic.Anthropic(...)` (real client is still constructed — the key is never validated up front) | **No fallback.** The first live call raises `anthropic.APIError`; `/chat` (`app/main.py`) catches it and returns `502` with `detail="LLM provider error: ..."` — a visible failure, not a silent degrade to offline mode. Presence of a key, not its validity, is what selects the client at startup (§6.6 below), so an invalid key looks identical to a valid one until the first real call is made. |

**Approach chosen: direct Claude API call by default, with an offline fallback.** The
orchestrator always tries to use the real Anthropic API; only when no `ANTHROPIC_API_KEY`
is present does it substitute a rule-based stand-in, entirely automatically, with no flag
or configuration needed beyond the key's presence. Mechanically, this works by swapping
which client object gets constructed, not by changing the agent flow itself:
`run_agent_loop` (§6.1) already treats its Claude client as a duck-typed dependency —
it only ever calls `client.messages.create(**kwargs)` and reads `.content` off the result;
it never imports `anthropic` or constructs a client itself. `app/main.py`'s `create_app(...)`
is the single place a concrete client gets constructed and injected. `OfflineResponder`
(`app/offline_responder.py`) is a second implementation of that same duck-typed interface:
a deterministic, rule-based responder that answers the plan call with keyword-heuristic tool
selection (e.g. "prioritise" → `score_priority`; a tool ID pattern like `ETCH-07` present →
`get_equipment_history`) and answers the synthesis call by building a templated `AgentAnswer`
from the *real* tool results the deterministic router actually fetched — not canned text.

`app/main.py`'s bootstrap picks between them based on whether `ANTHROPIC_API_KEY` is set:

```python
anthropic_client=(
    anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    if os.environ.get("ANTHROPIC_API_KEY")
    else OfflineResponder()
),
```

That one conditional is the entire change to existing files. `loop.py`, `tools.py`,
`schemas.py`, grounding, injection scanning, and audit logging all run completely
unmodified in either mode — the same plan→execute→synthesise flow, the same tool contracts,
the same structured `AgentAnswer` shape, the same audit trail. Only the source of the two
LLM-shaped answers changes. This is the same seam the test suite already exploits:
`FakeAnthropicClient` (used throughout `tests/`) proves the exact same `run_agent_loop` code
runs correctly against a non-Anthropic client, with zero real API calls.

Alternatives considered and rejected:

| Approach | Why rejected |
|---|---|
| Skip planning/synthesis entirely when no key; just run a fixed set of tool calls and return raw results | Does not preserve the tool-calling *flow* the brief requires — no real planning happens, and there's no structured recommendation/evidence/assumptions (§6.2's required answer shape), which is exactly what's being assessed. |
| A separate `?mock=true` opt-in flag or parallel code path | Two flows to keep in sync instead of one; doesn't help someone who just runs `docker compose up` without knowing to set a flag — the goal is zero-friction, not an extra step. |

Honesty/transparency: `OfflineResponder`'s answers always carry `confidence: "low"` and an
explicit `assumptions` entry stating the response was generated by offline demo mode with
rule-based tool selection, not live Claude reasoning — so nobody mistakes a heuristic answer
for a real model's output. The offline synthesis step always reports its evidence as
sufficient (never triggers the revision round in §6.1) to keep the responder's scope small;
the revision path is already thoroughly covered by `test_loop.py`'s unit tests against the
real Claude-shaped contract, so demo mode doesn't need to re-prove it.

Known coupling, disclosed rather than hidden: because `OfflineResponder` must satisfy the
exact same method signature the real Anthropic SDK exposes, it cannot receive `trace`'s
structured tool-call list directly for the synthesis call — it only sees the same rendered
prompt text `_build_synthesis_prompt` (§6.2) already builds for the real Claude call, and
recovers the tool results by parsing that text back out. A dedicated test pins
`_build_synthesis_prompt`'s exact text format against this parser, so any future change to
that format fails a test immediately instead of silently breaking offline mode.

## 7. Observability

- Structured JSON logs to stdout from every service (request id, method, path, latency,
  status) — visible via `docker compose logs`.
- `agent-orchestrator` additionally writes one `audit_log` row per request: user query,
  the ordered list of tool calls made (name, input, result, error — no per-call
  latency), any prompt-injection flags raised, and the final answer (whose `evidence`
  entries each carry a `verified` flag, the only place the grounding-check outcome is
  recorded — there is no separate grounding-outcome column), plus `created_at`.
  Retrievable via `GET /audit/{request_id}`, which is what answers "show me the
  evidence behind your recommendation" at the trace level (the structured `evidence`
  field in the chat response answers it at the answer level). Per-request total latency
  is not stored in this table; it is captured separately in the structured stdout logs
  described above.
- A `request_id` (UUID) is generated per `/chat` call and passed as a header on every
  downstream REST call, so log lines across services can be correlated by grepping one
  ID — a lightweight stand-in for distributed tracing.
- **Audit-log schema is migrated on every startup, not just created once.** `CREATE TABLE IF NOT
  EXISTS` only handles a brand-new database — it does nothing for a table that already
  exists under an older schema. Because `audit_log`'s SQLite file lives in a Docker
  named volume that survives image rebuilds, a schema change (e.g. the
  `schema_validation_failures` column added alongside the structured-output hardening
  in §6.2) would otherwise crash every `INSERT` against a pre-existing volume with
  `sqlite3.OperationalError: table audit_log has no column named ...`. This surfaced
  during development via a live `/chat` call against a volume created before that
  column existed, and was fixed at the source rather than patched around it:
  `app/audit.py`'s `connect()` now runs a small migration on every startup —
  `PRAGMA table_info(audit_log)` to see what columns actually exist, then
  `ALTER TABLE ADD COLUMN` for anything missing. This is deliberately not a general
  migration framework; it's scoped precisely to make the failure mode that actually
  occurred here impossible to hit again, and a regression test reproduces the original
  error against a simulated pre-existing database to prove it.

## 8. Security / access-control assumption

**What was built: RBAC assumptions, not RBAC enforcement.** This is deliberately
scoped as role-based access control *assumptions*, not a full RBAC implementation: a
caller-asserted framing hint that changes how the final recommendation is worded, not
real access control. `/chat` accepts an optional
`X-User-Role: engineer|manager` header (`app/main.py`); anything absent or unrecognised
silently normalises to `engineer`. The role is threaded through `run_agent_loop` into
the synthesis and revision system prompts only (`app/loop.py`'s `_ROLE_FRAMING` /
`_role_framed_system_prompt`), never into the planning call and never into which tools
get called. A manager gets a synthesis prompt that asks the model to lead with
downtime/cost/cross-tool trends rather than alarm codes and step-by-step procedure; an
engineer gets the reverse — but every role fetches identical tool results, sees
identical evidence, and gets an answer built from the identical audit-logged data.
Nobody sees more or less data than anyone else; this is framing, not filtering.

**The production security picture behind this is already fully specified, not left
vague.** In production, the orchestrator would sit behind an API gateway performing
OIDC-based authentication, service-to-service calls would carry a short-lived JWT or
use mTLS, and the orchestrator would enforce role checks (engineer vs. manager) before
including certain fields (e.g. cost/downtime rollups) in a response.

No authentication or authorization is implemented in this demo, however — there is no
login, no session, no verification of who is actually calling `/chat`, and the
`X-User-Role` header above is entirely caller-asserted and unenforced: anyone can claim
`X-User-Role: manager`. This is called out as documented future work rather than
built. Explicitly out of scope for the same reason: per-role data or tool restrictions, and a
real login/session system. Building those would be the actual "RBAC enforcement" item
already listed as deferred future work in §2 — the stub above deliberately stays
within this narrower "assumptions" scope, without pretending to be real access
control, and is not a substitute for the real auth/authz described above.

**The path from here to real auth is already concrete, though, not just a
placeholder.** The most immediate next step is backing the `X-User-Role` header with
real authentication: signed JWTs carrying a role claim, verified by
`agent-orchestrator` middleware, so a role is cryptographically asserted rather than
trusted from an unauthenticated header. This is a direct extension of the
RBAC-assumptions stub above — no user store or login UI needed for a demo-scale
version, just token issuance/verification with a shared signing secret — unlike the
OIDC-gateway/mTLS picture above, which is real production infrastructure this demo
doesn't need yet. See `README.md`'s "What the candidate would improve with more time"
for this framed alongside the codebase's other concretely-next-buildable item
(dependency pinning).

## 9. Trade-offs & future production considerations

### 9.1 LLM cost/latency, and when the live tool-use loop is the wrong choice

A live tool-use loop makes 3-6 Claude calls per query, and — because each call resends
the full conversation so far (system prompt, tool schemas, all prior tool results) —
cost grows with how many rounds a given query needs, not just with the query itself.
At Claude Sonnet 5 introductory pricing ($2/MTok input, $10/MTok output through Aug
2026; $3/$15 after):

| Query type | Tool-use turns | Approx. cost |
|---|---|---|
| Simple (e.g. "prioritise today's tickets") | 2-3 | ~$0.017 |
| Typical (e.g. "summarise ETCH-07 alarms + likely causes") | 3-4 | ~$0.027 |
| Heavy (compare historical cases + draft follow-up note) | 5-6 | ~$0.05 |

**Known limitation: wall-clock latency, not just $ cost.** The table above is about
spend; a separate, real-world-observed problem is response time. Calling Claude is
simply slow: a live `/chat` call was measured at **~51s** for a typical multi-ticket
prioritisation-plus-reasoning query. The cause is
structural, not a bug to be tuned away: every Claude call in the bounded hybrid (§6.1)
is sequential and blocking — the next can't start until the previous finishes
generating, so the ~51s is really the sum of 2-3 separate calls, not one slow call.
The plan call (Haiku) is comparatively fast, since it only has to name a tool; almost
all of the time is spent in synthesis (Sonnet), because an LLM generates its response
token by token — there's no way to skip ahead to the end — and the synthesis step has
to produce a full structured JSON answer (recommendation, an evidence list, assumptions,
confidence, next action, sometimes a follow-up note), so its generation time scales
with how much of that text there is. Sonnet is also inherently slower per token than
Haiku, which compounds this: the step doing the heavy reasoning is also the step
running on the slower model, by design (§9.1's model tiering). A query that also
triggers the one optional revision round pays for a second full synthesis generation
back to back, roughly doubling the already-slowest part of the pipeline — which is
consistent with worst-case queries landing in the 40-50s range. Critically, the live
tool-use loop rejected above would not fix this: it makes *more* sequential calls (3-6,
uncapped) than the bounded hybrid's ceiling of 3, so it is equally or more
latency-bound, never less — the latency problem and the cost-predictability problem
this section already argues for are the same problem, solved by the same shape. (One
contributing factor has since been addressed: `max_tokens=1500` on the synthesis call
was too low for a multi-ticket answer, causing `stop_reason="max_tokens"` truncation
and a failed JSON parse on some queries — raised to 4096, with a regression test
asserting the actual API call kwarg, `app/loop.py`. Before that, some slow calls
were also *wasted* time: a truncated, unparseable response still cost the full
generation time before falling back to a template.) See `README.md`'s `/chat` latency
bullet under "Known limitations and assumptions" for this same finding stated as a
limitation.

For this demo's expected volume (a handful of engineers/managers, occasional queries)
the $ cost above is immaterial — total spend across development and a recorded demo
comes to a few dollars. It stops being immaterial, and the live loop stops being the
right choice,
in a deployment where cost predictability and auditability matter as much as raw
average cost — a large-scale, cost-sensitive procurement is the clearest example:

- **Cost predictability over lowest average cost.** Budget cycles and procurement
  want a bounded, predictable per-query cost, not a variable 3-6 call range. A
  **plan-then-execute** pattern (rejected above for being less adaptive) gives a
  fixed ceiling of 2 LLM calls per query. A **bounded hybrid** — one Claude call
  produces a plan, a deterministic router executes it, one Claude call synthesises
  the answer, and *at most one* additional plan+execute round is allowed if the
  synthesis step flags the evidence as insufficient — keeps most of the live loop's
  adaptiveness while capping worst-case cost at 3 calls instead of 6.
- **Auditability of the reasoning process, not just the answer.** A discrete "plan"
  artifact that can be logged and reviewed before execution is a stronger compliance
  story than an autonomous loop where "the AI decided on its own" which services to
  call and in what order — relevant beyond just cost.
- **Query routing before paying for reasoning at all.** Not every query needs an LLM.
  "Which tickets should I prioritise" is fully answered by `recommendation-service`'s
  deterministic scoring plus a templated sentence — zero LLM calls. Only queries that
  genuinely require synthesising unstructured evidence (root-cause hypotheses,
  cross-referencing historical cases) need Claude at all. A cheap classifier ahead of
  the agent (keyword-based, no LLM required) routing prioritisation-type queries to
  the deterministic path directly would cut LLM spend more than any change to the
  orchestration pattern.
- **Model tiering.** Orthogonal to the above: use a cheaper model (e.g. Haiku) for
  low-judgment steps (intent classification, plan generation) and reserve Sonnet for
  the step that needs real reasoning (final synthesis over evidence). Stacks with
  either orchestration pattern.
- **Deployment topology, not just orchestration.** Semiconductor fab operational data
  can be strategically sensitive. A regulated deployment might have a hard
  data-residency requirement rather than a soft cost preference — Claude via a
  sovereign or dedicated-cloud offering, or an on-prem/open-weight model, rather than
  the public API. That is a different axis entirely from how the agent plans and
  calls tools, and would need to be resolved before the cost question is even
  relevant.

The first bullet's bounded hybrid and model-tiering levers were adopted into the demo
itself (§3.1, §6.1) after this analysis — a cost-sensitive/regulated deployment context
was the deciding factor, not a raw agentic-ness score. Query routing (skipping the LLM
entirely for purely deterministic queries) and deployment topology (sovereign-cloud/
on-prem) were not implemented, and remain documented future work: query routing would
add a pre-agent classification step outside this plan's scope, and topology is an
infrastructure decision independent of the application code.

### 9.2 Deferred production-scale infrastructure

Four items in this design follow the same shape: async request submission, semantic
search for knowledge retrieval, a circuit breaker around downstream calls, and
event-driven ticket ingestion. Each is a real, well-understood production pattern
solving a genuine problem — but none of these issues exist at the current scale of 
this demo and implementing solutions for them now would introduce unnecessary 
complexity to address problems that have not yet arisen, resulting in 
an overengineered design.

**Async request submission** — returning a job ID immediately and having the client
poll or receive a webhook once the answer is ready, instead of blocking on the HTTP
connection — solves bursty, high-concurrency load against a rate-limited LLM API,
where many simultaneous users would otherwise all block on open connections to the
same downstream (Anthropic) API. (Separate from event-driven *inter-service* calls,
§3.1's table, already rejected there for different reasons.) This demo's interaction
model is conversational — the user expects an answer in the same exchange, not "check
back later" — the expected concurrency is a handful of engineers/managers rather than
hundreds of simultaneous requests, and two features work naturally under a synchronous
model but would need real extra design under an async one: the "show me the evidence"
follow-up (only meaningful immediately after an answer exists) and the
human-in-the-loop "Save follow-up" action (reacts to a draft note still present in the
response it's replying to). Revisit once real concurrent production load exists — not
before.

**Vector database / semantic search for knowledge retrieval** fixes TF-IDF's real
blind spot — no stemming or synonym matching, so a query for "troubleshoot" won't
match "troubleshooting" — which gets worse as a document corpus grows large and
varied. This demo's corpus is 3 documents (§5), where TF-IDF is an exact fit: keyword
overlap reliably finds the relevant document at that size. Adopting semantic search
now would mean four real costs for no benefit yet: an external embedding-API
dependency that breaks this system's zero-setup, works-fully-offline property (§6.6);
real vector-DB infrastructure unjustified at a 3-document corpus; reduced
explainability, since a TF-IDF match traces to the exact shared words while an
embedding match doesn't, cutting against this system's evidence-grounding theme
(§6.3); and a real risk of regressing on exact technical-identifier matching (alarm
codes like `RF-OVR-REFL`, tool IDs like `ETCH-07`) that TF-IDF matches precisely and
embeddings can blur. Revisit once the corpus grows large enough that keyword overlap
genuinely starts missing relevant documents — not before.

**A circuit breaker around downstream service calls** protects against cascading
failure: under real concurrent traffic, a flat retry policy means every caller keeps
hammering an already-degraded service with retries, which can turn one degraded
service into a full outage. This demo is 5 services on one Docker network handling a
handful of sequential requests, not hundreds of concurrent callers — the flat
retry-once policy already in place (§6.5) is proportionate to that reality, recovering
from a single transient failure without the overhead of tracking failure rates and
open/half-open/closed state for a cascading-failure risk that doesn't exist at this
scale. Revisit once there's real traffic volume where a degraded service could
actually get hammered by concurrent retries — not before.

**Event-driven or scheduled ticket ingestion** keeps the agent's ticket data in sync
with a real, constantly-changing upstream ticketing/CMMS system, via a webhook
pipeline or scheduled polling. This demo's `ticket-service` seeds once from a static
JSON file at container startup (§5) — there is no external system to sync with, since
tickets are seeded or created directly via the API. Building a queue/scheduler now
would be genuinely new infrastructure solving a data-freshness problem that doesn't
exist yet. Revisit once this is backed by a real, changing ticketing system — not
before.

Two more items round out the future-work picture, but belong to a different category
— not deferred to avoid over-engineering, but for two unrelated reasons:

- **Recommendation scoring engine**: `recommendation-service` currently ranks tickets
  with a hand-picked weighted formula (0.4×severity + 0.3×downtime + 0.2×recurrence +
  0.1×age; §4.4), including keyword/substring matching for recurrence detection
  specifically — transparent and fully auditable, but the weights were chosen, not
  learned, and a fixed formula can't pick up on interactions between signals (e.g. two
  moderate factors compounding into something more urgent than either alone). A
  production version would swap this for a small, still-explainable ML model (e.g.
  gradient-boosted trees over the same feature set) — or add an LLM-assisted scoring
  step — trained on real historical outcomes: which tickets actually got escalated,
  how fast, and what happened after. Unlike the four items above, this one is
  genuinely *blocked*, not just deferred: there's no real historical outcome data to
  train or evaluate against yet, only synthetic seed tickets, so building it now would
  mean training on data that doesn't reflect anything real. See `README.md`'s "What
  the candidate would improve with more time" for this framed alongside the two
  actually-buildable-now items.
- **Audit storage**: SQLite is fine for a demo; production would want an append-only
  store (e.g. a dedicated audit table in Postgres, or a log pipeline) with a retention
  policy. This is a straightforward scale upgrade rather than a build-vs-defer
  judgment call — there's no real design question here, just more storage
  infrastructure than a demo needs.

## 10. Repository structure

```
service-centre-agent/
  docker-compose.yml
  .env.example
  README.md
  docs/
    superpowers/specs/           (this file — the design rationale and architecture doc)
  common/
    logging_middleware.py        (canonical source, see below — do not hand-edit copies)
  services/
    ticket-service/
      app/logging_middleware.py  (generated copy)
    equipment-history-service/
      app/logging_middleware.py  (generated copy)
    knowledge-service/
      app/logging_middleware.py  (generated copy)
    recommendation-service/
      app/logging_middleware.py  (generated copy)
    agent-orchestrator/
      app/logging_middleware.py  (generated copy)
      static/                    (chat UI + dashboard)
  data/
    seed/                        (synthetic JSON source data)
  scripts/
    sync-common.py                (regenerates the 5 logging_middleware.py copies from common/;
                                    --check mode fails CI on drift)
  tests/
    ticket-service/
    equipment-history-service/
    knowledge-service/
    recommendation-service/
    agent-orchestrator/          (includes mocked tool-loop integration test,
                                   test_evaluation_harness.py — behavioural assertions
                                   against OfflineResponder, no API key needed)
```

**Shared `logging_middleware.py`, deduplicated.** The structured-logging middleware
(request ID, method, path, latency, status — §7) was byte-identical across all 5
services. Rejected two more obvious fixes in favour of a third: a shared pip package
would introduce a runtime version-coupling dependency between services meant to stay
independently deployable; a Docker-build-context restructure (each service importing
a sibling directory) would break each service's independent local venv + `pytest`
workflow, since Docker build contexts are isolated per service by design. Instead,
`common/logging_middleware.py` is the single edited source, and
`scripts/sync-common.py` regenerates the 5 per-service copies — one real source of
truth to edit, with the file still physically present wherever each service's
`from app.logging_middleware import ...` and local test workflow expect it.
`sync-common.py --check` exits non-zero if any copy has drifted from the canonical
source, so a hand-edited copy can't silently diverge — designed to run as a CI gate
(`.github/workflows/ci.yml` drafts this), not yet confirmed running in a live CI
pipeline for this repo.

## 11. Testing strategy

- `recommendation-service`: unit tests on the scoring formula (pure functions, no I/O).
- `knowledge-service`: unit tests on search ranking for known queries.
- `agent-orchestrator`: unit tests on the Pydantic output schema validation and the
  ID-grounding check; one integration test that runs the full bounded
  plan→execute→synthesise flow (including the one-revision path) against the four
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
| README + architecture doc | `README.md`, this design spec |
