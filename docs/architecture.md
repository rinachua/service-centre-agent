# Architecture

This document is the submission-facing architecture write-up. It summarises and
supersedes the working design spec at
`docs/superpowers/specs/2026-07-11-service-centre-agent-design.md`, which contains
the full alternatives analysis.

## System diagram

```
                         Chat UI (static JS)
                         served by orchestrator
                                 |
                            POST /chat
                                 v
                        agent-orchestrator (:8000)
                        FastAPI + bounded
                        plan→execute→synth
                        audit_log (SQLite)
              REST      /       |        \      REST
     +------------------+       |         +------------------+
     v                          v                            v
ticket-service (:8001)  equipment-history-      knowledge-service (:8003)
SQLite                  service (:8002)         TF-IDF search
     ^                  SQLite
     |
     +---- recommendation-service (:8004), called by orchestrator with
           tickets + history already fetched (stateless, deterministic)
```

All inter-service calls are synchronous REST (`httpx`) over the Compose network. The
orchestrator is the only service that talks to Claude and the only service the UI
talks to — other services are never called directly by the client, and never call
each other.

## Service decomposition and responsibility boundaries

| Service | Responsibility | Owns data | Never does |
|---|---|---|---|
| ticket-service | Open/closed tickets, follow-up notes | tickets, followups (SQLite) | Call other services |
| equipment-history-service | Asset status, alarm/maintenance history | assets, history (SQLite) | Call other services |
| knowledge-service | SOP/troubleshooting/shift-note retrieval | in-memory TF-IDF index over 3 docs | Call other services |
| recommendation-service | Deterministic priority scoring | none (pure function) | Call the LLM, call other services |
| agent-orchestrator | Plan, call tools, ground, synthesise, audit | audit_log (SQLite) | Get called by anything except the browser |

## API/interface definitions

Each REST service and the orchestrator's endpoints are documented in the plan's
per-task "Interfaces" sections; see
`docs/superpowers/plans/2026-07-11-service-centre-agent-implementation.md` Tasks 3-10
for the authoritative list of routes, request/response shapes, and status codes.
Every downstream call from the orchestrator carries `X-Request-ID` so log lines
across services can be correlated for one user query.

## Agent workflow: bounded plan→execute→synthesise

The orchestrator does NOT run an open-ended Claude tool-use loop. It runs a bounded,
at-most-3-Claude-call flow, chosen over a live tool-use loop specifically for cost
predictability and auditability in a cost-sensitive/regulated deployment context (see
spec §3.1 and §9.1 for the full comparison and the gov-agency framing that drove it):

1. **Plan** (1 call, `CLAUDE_PLANNER_MODEL`, default `claude-haiku-4-5-20251001`):
   Claude receives the user query and the 7-tool schema (`get_tickets`, `get_ticket`,
   `get_equipment`, `get_equipment_history`, `search_history`, `search_knowledge`,
   `score_priority`) with `tool_choice={"type": "any"}`, forcing it to name every tool
   it wants called in this single turn — no free text, no iteration, no seeing results
   before deciding.
2. **Execute** (deterministic, no LLM): the orchestrator calls each planned tool
   against the relevant downstream REST service and collects results.
3. **Synthesise** (1 call, `CLAUDE_MODEL`, default `claude-sonnet-5`): Claude receives
   the user query and all tool results, and must return a JSON object with an `answer`
   (matching the `AgentAnswer` schema: recommendation, evidence, assumptions,
   confidence, next_action, optional followup_note), a `sufficient` boolean, and an
   optional `additional_tool_request`. If the JSON is malformed, the orchestrator falls
   back directly to a templated answer built from raw tool results — there is no
   repair-prompt round-trip, to keep the cost cap strict.
4. **Optional single revision** (at most 1 more call): only if `sufficient` is false
   and an `additional_tool_request` was given, the orchestrator executes that one extra
   tool call and makes exactly one final revision-synthesis call. The revision
   response has no `sufficient`/`additional_tool_request` fields, so a second revision
   is structurally impossible — 3 Claude calls is the hard ceiling per request, never
   more. `AgentTrace.revised` records whether this path fired, for auditability.

Cross-cutting to every step:
- **Evidence grounding**: every `record_id` cited in the final answer is checked
  against IDs actually seen in tool results that session; unverifiable IDs are
  flagged rather than trusted.
- **Failure handling**: downstream calls get a 3s timeout and 1 retry; on repeated
  failure the orchestrator continues with partial evidence and records the gap in
  `assumptions`, never surfacing a bare 500 to the user.
- **Prompt-injection scanning**: every planned tool call's input is scanned before
  execution, in both the initial plan and the one optional revision round.
- **Human-in-the-loop writes**: the agent only ever drafts a follow-up note. Saving
  it to `ticket-service` is a separate, explicit UI action.

## Key trade-offs

See spec §3.1 for the full pros/cons table comparing this bounded hybrid against a
live Claude tool-use loop, a plain plan-then-execute flow with no revision, and
event-driven (queue-based) inter-service calls; §9.1 for the LLM cost/latency figures
and the gov-agency cost/auditability/model-tiering/deployment-topology considerations
that motivated the pivot away from the live tool-use loop; §9.2 for why async request
submission (job-queue decoupling of the `/chat` endpoint itself) was considered
separately and rejected for this demo; and the deterministic-vs-LLM boundary rationale
for `recommendation-service`.

## Future production considerations

See spec §9 (LLM cost/latency at scale — including the cost table and model-tiering
rationale in §9.1, and the async-submission rejection in §9.2 — vector-store
retrieval, recurrence-detection model, audit storage, circuit breakers, query routing
to skip the LLM entirely for deterministic queries, and GovCloud-hosted/on-prem
deployment topology for data residency) and this document's "What I'd improve with
more time" section in `README.md`. Of these, the bounded hybrid flow and model
tiering (Haiku planner / Sonnet synthesis) were actually adopted into this demo;
query routing and deployment topology remain documented future work, not built here.
