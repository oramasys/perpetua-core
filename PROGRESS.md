# Salvage Translation Progress — RC-1

**Plan:** `orama-system/docs/superpowers/plans/2026-05-17-salvage-translation-v1-discovery.md`
**Generation labels** (per Canonical Repo Registry):
- **v1-legacy**: `diazMelgarejo/Perpetua-Tools` on `feat/ip-aware-discovery`
- **v2-planning**: this repo on `feat/salvage-plugins-rc1`; `oramasys/oramasys` on `feat/dispatch-discovery-bridge`
- **cross-cutting**: `orama-system/docs/` (specs, plans, LESSONS)

## Local hardware available (verified 2026-05-17)

| Endpoint | Models (relevant subset) |
|---|---|
| Ollama Mac `localhost:11434` | `qwen3.5:9b-nvfp4`, `bge-m3:latest`, `qwen3-coder:480b-cloud`, `glm-5.1:cloud`, `gpt-oss:120b-cloud` |
| LM Studio Mac `localhost:1234` | `qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2`, `qwen3.5-9b-mlx`, `gemma-4-e4b-it` |
| LM Studio Win `192.168.254.103:1234` | `qwen3.5-27b-claude-4.6-opus-reasoning-distilled-v2`, `gemma-4-26b-a4b-it`, `qwen3.5-9b-mlx` |

## Task ledger

| # | Track | Gen | Task | Owner | Status | Claimed | Done | Commit |
|---|-------|-----|------|-------|--------|---------|------|--------|
| 0 | — | cross | branches + PROGRESS.md | opus-orchestrator | DONE | 2026-05-17 | 2026-05-17 | a579624 |
| 1 | A | v1-legacy | `perpetua/discovery/backend.py` (Backend dataclass) | subagent-A1 | DONE | 2026-05-17 | 2026-05-17 | 9b11e9d |
| 2 | A | v1-legacy | `perpetua/discovery/probe.py` (async health_probe) | subagent-A1 | DONE | 2026-05-17 | 2026-05-17 | 7e4a40b |
| 3 | A | v1-legacy | `perpetua/discovery/registry.py` (autodetect + register_by_ip) | subagent-A1 | DONE | 2026-05-17 | 2026-05-17 | 06c1da3 |
| 4 | A | v1-legacy | `perpetua/discovery/selector.py` (tier+task routing) | subagent-A1 | DONE | 2026-05-17 | 2026-05-17 | 8d42f2e |
| 4b | A | v1-legacy | wire `agent_launcher.resolve_backend_for_spec` | subagent-A1 | DONE | 2026-05-17 | 2026-05-17 | bf15d0d |
| 5 | B | v2-planning | engine: `max_steps` cycle guard | subagent-B1 | DONE | 2026-05-17 | 2026-05-17 | a3712b2 |
| 6 | B | v2-planning | engine: `set_entry` + `compile()` → CompiledGraph | subagent-B1 | DONE | 2026-05-17 | 2026-05-17 | ad67577 |
| 7 | C | v2-planning | plugin: `routing.LabelRouter` (needs Task 6) | subagent-C7 | DONE | 2026-05-17 | 2026-05-17 | 283af1a |
| 8 | C | v2-planning | plugin: `tool_node.ToolNode` (async subprocess) | subagent-C8 | DONE (race-bundled) | 2026-05-17 | 2026-05-17 | 8eaba56 |
| 9 | C | v2-planning | plugin: `validator.Validated` (pre/post gates) | subagent-C9 | DONE (race-bundled) | 2026-05-17 | 2026-05-17 | 8eaba56 |
| 10 | C | v2-planning | plugin: `interrupt_guard.resume_policy` | subagent-C10 | DONE | 2026-05-17 | 2026-05-17 | a7c9772 |
| 11 | C | v2-planning | plugin: `parallel.parallel_dispatch` (Send fan-out) | subagent-C11 | DONE (commit-of-record for 8/9/11) | 2026-05-17 | 2026-05-17 | 8eaba56 |
| 12 | C | v2-planning | `perpetua_core/message.py` typed wrapper (OQ17) | subagent-C12 | DONE | 2026-05-17 | 2026-05-17 | 309c60a |
| 13 | D | v2-planning | canonical `perpetua_core/discovery/` (verbatim port from v1) | opus-orchestrator | DONE | 2026-05-17 | 2026-05-17 | 222450b |
| 14 | D | v2-planning | wire `oramasys/orama/graph/perpetua_graph.py` dispatch_node | subagent-D14 | DONE | 2026-05-17 | 2026-05-17 | 21605f6 |
| 15 | E | cross | Hypothesis property tests for engine invariants | opus-orchestrator | DONE | 2026-05-17 | 2026-05-17 | 8b1a3f1 |
| 16 | E | cross | full regression sweep + PROGRESS DONE march | opus-orchestrator | DONE | 2026-05-17 | 2026-05-17 | _this commit_ |

## Claim protocol

1. Edit your row: set Owner to `<agent-id>` (e.g., `subagent-A1`, `codex-mac`, `gemini-pro`), set Status to `CLAIMED`.
2. Commit the claim immediately on the appropriate branch: `chore(progress): claim task N (<agent-id>)`.
3. If another agent already claimed the row you wanted: pick another row.
4. On completion: set Status to `DONE`, fill Commit SHA, commit + push (locally only — push is orchestrator's call).
5. If blocked: set Status to `BLOCKED: <reason>`. Orchestrator handles.

## Wave plan (orchestrator-owned)

**Wave 1A — parallel fan-out (7 agents simultaneously):**
- A1 → Tasks 1, 2, 3, 4, 4b (sequential within agent, v1-legacy repo)
- B1 → Tasks 5, 6 (sequential within agent, v2 engine.py)
- C8 → Task 8 (v2 plugin: tool_node)
- C9 → Task 9 (v2 plugin: validator)
- C10 → Task 10 (v2 plugin: interrupt_guard)
- C11 → Task 11 (v2 plugin: parallel)
- C12 → Task 12 (v2 message.py)

**Wave 1B — sequential after B1 completes Task 6:**
- C7 → Task 7 (routing plugin — depends on `set_entry`)

**Checkpoint:** verify all three repo test suites green.

**Wave 2 — sequential:**
- D13 → Task 13 (verbatim copy v1 → v2 canonical discovery)
- D14 → Task 14 (wire perpetua_graph dispatch_node)

**Wave 3 — sequential:**
- E15 → Task 15 (Hypothesis invariants)
- E16 → Task 16 (regression + PROGRESS DONE)

---

## Final regression (2026-05-17)

| Repo | Generation | Tests |
|---|---|---|
| `oramasys/perpetua-core` @ `feat/salvage-plugins-rc1` | v2-planning | **56 passed** (32 baseline + 24 new) |
| `oramasys/oramasys` @ `feat/dispatch-discovery-bridge` | v2-planning | **5 passed** (4 baseline + 1 new) |
| `diazMelgarejo/Perpetua-Tools` @ `feat/ip-aware-discovery` | v1-legacy | **12 passed** (all new discovery suite) |

**Total: 73 tests across three repos, three generations, all green.**

## Race-condition note (Tasks 8, 9, 11)

During Wave 1A parallel dispatch, three subagents (C8 tool_node, C9 validator, C11 parallel) hit a `.git/index.lock` race. The serializing winner (C11) committed all three plugin files plus its own under one commit `8eaba56`, mis-labeled "Task 11". **Content is correct per plan** — each subagent verified its own writes against the plan; the bit-identical match was confirmed in their post-commit audits. No work was lost. Future plans should serialize commits inside parallel waves (e.g., orchestrator collects file artifacts and commits them) to avoid this. See LESSONS.md note 2026-05-17.

## Push policy

All three branches stay **local** until user reviews end-to-end on Mac+Win hardware (Mac Ollama `localhost:11434` + Win LM Studio `192.168.254.103:1234`). Only `orama-system/docs/` push happens immediately (cross-cutting docs).
