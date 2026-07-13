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
- A real Claude API key is expected and used for the LLM component when available
  (`ANTHROPIC_API_KEY` env var). If no key is set, the system automatically falls back to
  a deterministic, rule-based offline responder — see §6.6 — rather than failing; this
  satisfies the assessment brief's §8 constraint for a documented local/mock substitute.
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
                         │   (FastAPI + bounded     │
                         │   plan→execute→synth)    │
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

**Agentic AI components, named explicitly.** This is an agentic AI system, not a chatbot
wrapped around an LLM: `agent-orchestrator` is the agent, and it decomposes into the
standard planner/act/reason/verify roles, each mapped to a concrete piece of code below
rather than left implicit.

| Agentic role | What plays that role | Where it lives |
|---|---|---|
| **Perception (input)** | The static chat UI's `POST /chat` request — a natural-language user query, no structure required. | `agent-orchestrator` `/chat` endpoint (§6.4) |
| **Planner** | One Claude call (cheap model) that reads the query and the tool schema, and decides which tool(s) to invoke — never sees results before deciding, never free-texts. | `run_agent_loop`'s plan phase (§6.1) |
| **Tools / actions** | The only way the agent touches data — never direct DB access. Four distinct capabilities: read tickets, read equipment/alarm history, retrieve knowledge-base text (RAG), and get a deterministic priority score. | `ticket-service`, `equipment-history-service`, `knowledge-service`, `recommendation-service` (§4) |
| **Executor** | Deterministic router that turns the planner's tool requests into real REST calls, with timeout/retry/error-containment — no LLM involved in this step. | `ToolExecutor` (§4.5) |
| **Reasoner / synthesiser** | One Claude call (full model) that turns raw tool results into the structured final answer — recommendation, evidence, assumptions, confidence, next action. | `run_agent_loop`'s synthesise phase (§6.1, §6.2) |
| **Self-critique / recovery** | The synthesiser judges its own evidence and can request exactly one more planner→executor→synthesiser round if insufficient — capped, not open-ended. | The "optional single revision" step (§6.1) |
| **Verifier (grounding)** | Checks every cited `record_id` in the final answer against IDs actually returned by tool calls that session; flags anything unverifiable rather than trusting it. | `verify_evidence` / `extract_known_ids` (§6.3) |
| **Memory (per-request, not conversational)** | Persists the full tool-call trace, injection flags, and final answer for later audit/replay. Each `/chat` call is independent — there is no multi-turn conversation memory across requests. | `audit_log` SQLite table, `GET /audit/{request_id}` (§7) |
| **Action gate (human-in-the-loop)** | The agent only ever *drafts* a follow-up note; nothing is written to `ticket-service` without an explicit, separate human action. | UI "Save follow-up" button → `POST /tickets/{ticket_id}/followups` (§6.4) |
| **Fallback planner/reasoner** | Deterministic, rule-based stand-in for the planner and synthesiser roles above when no LLM is available — same roles, same flow, different implementation. | `OfflineResponder` (§6.6) |

### 3.1 Why this shape (vs. alternatives considered)

| Approach | Description | Pros | Cons | Decision |
|---|---|---|---|---|
| **Bounded hybrid: plan → execute → synthesise, with one capped revision** (chosen) | One Claude call (cheap model) turns the query into a tool-call plan; a deterministic router executes it; one Claude call (full model) synthesises the answer and flags whether evidence was sufficient. If not, exactly one more execute→synthesise round runs — never more. | Fixed worst-case cost ceiling (2 calls typical, 3 max, vs. an open-ended 3-6) and therefore predictable at procurement/budget-planning time; the "plan" is a discrete, loggable artifact — stronger auditability story than an autonomous loop deciding its own next step; still recovers from a wrong first plan once, unlike plain plan-then-execute; cheap model on the planning call, full model only where reasoning quality matters (model tiering). | Less adaptive than a fully open loop in the rare case where more than one revision would genuinely help; the plan/synthesis split adds a small amount of orchestration complexity (a JSON contract between the two phases) that a single continuous loop doesn't need. | **Chosen.** Revised from an initial live tool-use loop after weighing cost predictability and auditability for a cost-sensitive/regulated deployment context — see §9.1. |
| **Live Claude tool-use loop** | Claude sees each tool result and decides the next call itself, over REST calls to each service, for up to 6 rounds. | Most genuinely agentic option — the plan can change mid-investigation on every single tool result, not just once; naturally supports "show me the evidence" since every tool call is already logged. | More LLM round-trips per query (3-6 calls, variable and hard to predict) → higher and less predictable cost/latency. At Claude Sonnet 5 pricing (introductory, through Aug 2026): roughly **$0.017 for a simple query, $0.027 typical, up to ~$0.05 for a heavy multi-tool investigation** — see §9.1. An autonomous loop is also a weaker audit story than a discrete, reviewable plan. **Expensive to scale to production**: cost grows per-query with how many rounds each one needs, not a fixed ceiling, so spend scales unpredictably with query volume and complexity rather than linearly. | Considered first, superseded by the bounded hybrid above once cost predictability and auditability were weighted alongside "most agentic" — see §9.1. |
| **Plain plan-then-execute (no revision)** | One Claude call produces a JSON plan, a deterministic router executes it, a second Claude call synthesises the answer — always exactly 2 calls, no recovery path. | Cheapest and most predictable of all three — fixed 2 calls, always. | Can't adapt at all if the first plan turns out wrong; the bounded hybrid gets almost all of this pattern's cost benefit while keeping a capped recovery path, so this variant's extra restriction wasn't worth it. | Rejected — the bounded hybrid dominates it. |
| **Event-driven (queue) inter-service calls** | Orchestrator publishes tool-call requests to a broker (e.g. Redis/RabbitMQ); services subscribe and respond async. | Matches one of the brief's allowed interface styles; demonstrates async/event-driven patterns. | Adds broker infrastructure and response-correlation complexity disproportionate to a lightweight demo; brief explicitly says REST is acceptable. | Rejected. |

**Deterministic vs. LLM boundary.** `recommendation-service` does priority *scoring*
(a weighted formula: severity, downtime impact, recurrence, ticket age) — deterministic,
auditable, and cheap, because ranking a fixed set of tickets by known fields doesn't
benefit from an LLM and benefits a lot from being reproducible. Root-cause *hypotheses*
are generated by Claude, because that requires synthesising unstructured evidence
(alarm patterns, SOP text, shift notes) in a way a fixed formula can't. This split is
called out explicitly because the assessment asks for it.

**Note on LLM availability.** The comparison above is about call *pattern* (how many
Claude calls, in what shape) and holds regardless of which client answers those calls.
A separate, orthogonal decision — what happens when no `ANTHROPIC_API_KEY` is available at
all — is documented in §6.6, not here.

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
handover notes. Implemented with hand-rolled, pure-Python TF-IDF + cosine similarity
(no `scikit-learn`) — no embedding API calls, no vector DB, deterministic and free to
run. Realistic for a 3-document corpus; documented in §9 as the first thing to swap for
a real vector store at production scale.

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
- `POST /chat` — conversational endpoint, runs the bounded plan→execute→synthesise flow
  (§6.1), returns the structured answer (§6.2).
- `GET /audit/{request_id}` — replay the full tool-call trace for a prior request.
- Holds the Claude client (planner model + synthesis model; automatically substituted
  with the offline `OfflineResponder` fallback when `ANTHROPIC_API_KEY` is unset — §6.6),
  tool schema (mapped 1:1 to the REST endpoints above), system prompts, grounding checks,
  retry/fallback logic, and the `audit_log` SQLite table.

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

### 6.1 Planning & tool use (bounded hybrid: plan → execute → synthesise, ≤1 revision)

Three phases, at most 3 Claude calls total (2 in the common case). Every "Claude call"
below is a real, live call to the Anthropic API whenever `ANTHROPIC_API_KEY` is set —
that's the default, full-fledged mode. When no key is present, `app/main.py` automatically
substitutes the deterministic `OfflineResponder` (§6.6) in place of the real client instead
— same steps, same call count, same JSON contracts, only the source of the plan/synthesis
answers changes. The rest of this section describes the live-mode behaviour; §6.6 covers
exactly how the fallback answers the same two call sites.

1. **Plan** (cheap model, e.g. Haiku, or the offline fallback's keyword heuristics — §6.6
   — if no key is set). System prompt + the same 7-tool schema (§4.5) + the user query,
   called with `tool_choice: "any"` so the response is only tool_use blocks — no free
   text, no execution yet. Claude may request multiple tool calls in this single
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

This recovers most of a fully open loop's value (a wrong first plan can still be
corrected once, e.g. escalate to knowledge-service if history alone didn't explain the
pattern) while keeping a hard, predictable cost ceiling — the deciding factor for the
cost-sensitive/regulated deployment context this was revised for (§9.1).

### 6.2 Response synthesis

Final answer must validate against a fixed Pydantic schema:

```
recommendation: str
evidence: list[{source: str, record_id: str, detail: str}]
assumptions: list[str]
confidence: "low" | "medium" | "high"
next_action: str
```

The synthesis call's raw output additionally carries `sufficient: bool` and an optional
`additional_tool_request` (consumed by the orchestrator to decide whether to run the
one allowed revision round; never exposed to the client — the `/chat` response only
ever contains the `answer` shape above, plus `followup_note` per §6.4).

If Claude's output doesn't validate against the expected schema at either synthesis
step, the orchestrator falls back immediately to a templated answer built directly
from the raw tool results already collected — there is deliberately no repair
round-trip in this design (unlike an earlier version of this spec), because a repair
call would reintroduce the unbounded-cost problem the bounded hybrid exists to remove.
Claude's structured-output reliability is high enough that this is an acceptable
trade: a malformed response is rare, and the fallback path already guarantees the user
never sees a bare error.

### 6.3 Evidence grounding (hallucination control)

Every `record_id` in the final answer is checked against the set of IDs actually
returned by tool calls during that session. Any unverifiable ID is flagged
`"unverified"` in the response rather than presented as fact, and logged as a warning
in the audit trail. This is a lightweight, mechanical check — not a substitute for
prompt-level grounding instructions, but a backstop against them failing.

Tool results (especially knowledge-service snippets, which contain free-text shift
notes) are treated as untrusted data: the system prompt explicitly instructs Claude to
treat tool output as information, not instructions. Separately, the orchestrator scans
every planned tool call's *input* (the arguments Claude requests a tool be called with,
not the data that tool returns) for common prompt-injection phrasing (e.g. "ignore
previous instructions") and logs a warning if found. This covers tool inputs only —
tool *results* are not separately scanned; the mitigation for injected content reaching
the synthesis model via a retrieved result is the system-prompt instruction above, not a
scanner. Documented as a basic mitigation, not a comprehensive defence; result-side
scanning is future work.

### 6.4 Write actions are human-gated

The agent can only *draft* a follow-up note (returned in the structured response). It
never calls `POST /tickets/{id}/followups` itself. Persisting the note is a distinct,
explicit action in the UI ("Save follow-up"), triggered by the user. This satisfies the
spirit of the "human-in-the-loop approval before creating or updating records" stretch
goal without adding an approval-workflow subsystem — the gate is structural, not
procedural.

### 6.5 Failure handling

- Each downstream REST call: 3s timeout, 1 retry with backoff.
- On repeated failure, the orchestrator records a synthetic "unavailable" result for
  that tool call and proceeds to the synthesis step anyway; the final answer's
  `assumptions` field records the gap explicitly (e.g. "equipment-history-service was
  unreachable; recommendation is based on ticket data only").
- The orchestrator never lets a downstream failure become an unhandled 500 to the user.

### 6.6 LLM-unavailable fallback (brief §8 compliance)

The brief's constraints (§8) require that the solution "use a real LLM API or a clearly
documented local/mock substitute if API access is not available," and that "any mock
should preserve the intended architecture and tool-calling flow." This section documents
that substitute.

**Summary**

| Condition | LLM client used | Behaviour |
|---|---|---|
| `ANTHROPIC_API_KEY` is set | `anthropic.Anthropic(...)` (real Claude API) | Full-fledged: real Claude Haiku plan call, real Claude Sonnet synthesis, exactly as described in §6.1. |
| `ANTHROPIC_API_KEY` is unset/empty | `OfflineResponder` (`app/offline_responder.py`) | Automatic fallback, no flag required: keyword-heuristic tool planning, templated synthesis built from real tool results. Same plan→execute→synthesise flow, same `AgentAnswer` shape, same grounding/audit/injection-scanning. Every offline answer carries `confidence: "low"` and an explicit assumption disclosing it wasn't generated by live Claude. |

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

## 8. Security / access-control assumption

No authentication or authorization is implemented in this demo. Documented assumption:
in production, the orchestrator would sit behind an API gateway performing OIDC-based
authentication, and service-to-service calls would carry a short-lived JWT or use mTLS,
with the orchestrator enforcing role checks (engineer vs. manager) before including
certain fields (e.g. cost/downtime rollups) in a response. This is called out as future
work rather than implemented, to keep the demo's scope aligned with "minimum functional
scope, polished."

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

For this demo's expected volume (a handful of engineers/managers, occasional queries)
this is immaterial — total spend across development and a recorded demo comes to a few
dollars. It stops being immaterial, and the live loop stops being the right choice,
in a deployment where cost predictability and auditability matter as much as raw
average cost — a government agency procuring this system is the clearest example:

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
  can be strategically sensitive. A real government deployment might have a hard
  data-residency requirement rather than a soft cost preference — Claude via a
  GovCloud-hosted offering, or an on-prem/open-weight model, rather than the public
  API. That is a different axis entirely from how the agent plans and calls tools,
  and would need to be resolved before the cost question is even relevant.

The first bullet's bounded hybrid and model-tiering levers were adopted into the demo
itself (§3.1, §6.1) after this analysis — a cost-sensitive/regulated deployment context
was the deciding factor, not a raw agentic-ness score. Query routing (skipping the LLM
entirely for purely deterministic queries) and deployment topology (GovCloud/on-prem)
were not implemented, and remain documented future work: query routing would add a
pre-agent classification step outside this plan's scope, and topology is an
infrastructure decision independent of the application code.

### 9.2 Why async request submission was not considered for this demo

Separate from event-driven *inter-service* calls (§3.1's table, rejected), a distinct
pattern is decoupling the *user's request* from computation — returning a job ID
immediately and having the client poll or receive a webhook once the answer is ready,
instead of blocking on the HTTP connection through the full tool-use loop.

This solves a specific, real problem: bursty, high-concurrency load against a
rate-limited LLM API, where many simultaneous users would otherwise all block on open
connections waiting on the same downstream (Anthropic) API, and queuing/backpressure
is needed to smooth that load. That is a genuine production-scaling concern.

It is not a problem this demo has, for three concrete reasons: the assessment's
interaction model is conversational (a chat UX where the user expects an answer in
the same exchange, not "check back later"); the expected concurrency is a handful of
engineers/managers rather than hundreds of simultaneous requests; and two features
work naturally under a synchronous model but would need real extra design under an
async one — the "show me the evidence" follow-up (only meaningful immediately after an
answer exists) and the human-in-the-loop "Save follow-up" action (reacts to a draft
note still present in the response it's replying to). Building queue/job
infrastructure to solve a load problem the demo doesn't have would be exactly the kind
of unnecessary infrastructure complexity the brief asks candidates to avoid (§8 of the
assessment brief: "do not spend excessive time on... infrastructure complexity").

Rejected for this demo; the correct lever to reach for once real concurrent production
load exists, not before.

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
| README + architecture doc | `README.md`, `docs/architecture.md` |
