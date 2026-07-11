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
                        FastAPI + Claude tool-use loop
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

## Agent workflow

1. **Planning & tool use**: Claude receives the user query and the 7-tool schema
   (`get_tickets`, `get_ticket`, `get_equipment`, `get_equipment_history`,
   `search_history`, `search_knowledge`, `score_priority`), and decides which to call,
   iterating for up to 6 rounds.
2. **Response synthesis**: the final turn must be a JSON object matching the
   `AgentAnswer` schema (recommendation, evidence, assumptions, confidence,
   next_action, optional followup_note). A malformed response gets one repair
   prompt before the orchestrator falls back to a templated answer built from raw
   tool results.
3. **Evidence grounding**: every `record_id` cited in the final answer is checked
   against IDs actually seen in tool results that session; unverifiable IDs are
   flagged rather than trusted.
4. **Failure handling**: downstream calls get a 3s timeout and 1 retry; on repeated
   failure the orchestrator continues with partial evidence and records the gap in
   `assumptions`, never surfacing a bare 500 to the user.
5. **Human-in-the-loop writes**: the agent only ever drafts a follow-up note. Saving
   it to `ticket-service` is a separate, explicit UI action.

## Key trade-offs

See spec §3.1 for the full pros/cons table comparing the live tool-use loop against
plan-then-execute and event-driven alternatives, and the deterministic-vs-LLM boundary
rationale for `recommendation-service`.

## Future production considerations

See spec §9 (LLM cost/latency at scale, vector-store retrieval, recurrence-detection
model, audit storage, circuit breakers) and this document's "What I'd improve with
more time" section in `README.md`.
