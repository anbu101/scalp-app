# Vendored skills — SKILLS_PACK_20261117

Open-source agent skills copied into this repo, pinned to one upstream commit each.
Installed by `apply_skills_pack.py` (fence `SKILLS_PACK_20261117`). QA Register: TD-036, TC-SKILLS-0001…0012.
`skills-pack.lock.json` holds the sha256 of every installed file; `python3 apply_skills_pack.py --verify` checks them.

| Skill | Upstream | Commit | Upstream path | Licence | Local changes |
|---|---|---|---|---|---|
| systematic-debugging | obra/superpowers | 22f0b463e7a5 | skills/systematic-debugging | MIT (LICENSE in folder) | `superpowers:<name>` references → `<name>` |
| verification-before-completion | obra/superpowers | 22f0b463e7a5 | skills/verification-before-completion | MIT (LICENSE in folder) | none |
| test-driven-development | obra/superpowers | 22f0b463e7a5 | skills/test-driven-development | MIT (LICENSE in folder) | `superpowers:<name>` references → `<name>` |
| webapp-testing | anthropics/skills | e4bb5d960d87 | skills/webapp-testing | Apache-2.0 (LICENSE.txt in folder) | none |
| differential-review | trailofbits/skills | 149d7b21f406 | plugins/differential-review/skills/differential-review | CC-BY-SA-4.0 (LICENSE in folder) | none |
| sharp-edges | trailofbits/skills | 149d7b21f406 | plugins/sharp-edges/skills/sharp-edges | CC-BY-SA-4.0 (LICENSE in folder) | none |
| property-based-testing | trailofbits/skills | 149d7b21f406 | plugins/property-based-testing/skills/property-based-testing | CC-BY-SA-4.0 (LICENSE in folder) | none |
| backtest-expert | tradermonty/claude-trading-skills | f91ef00cda33 | skills/backtest-expert | MIT (LICENSE in folder) | none |

## Notes for this project

- **Project doctrine wins.** Where a skill and `doctrine-and-workflow` differ (apply scripts, fences, dual tree, falsify-first), follow the project rules.
- **backtest-expert** is written for US stocks. Its DEPLOY/REFINE thresholds are generic; for our options strategies the falsify-first rule (per-year sign tests, leave-one-year-out, walk-forward) is the gate. `scripts/evaluate_backtest.py` is stdlib-only and writes to `--output-dir` only.
- **webapp-testing**: `scripts/with_server.py` starts servers with `shell=True`. Point it at the frontend build + a mocked backend only — never at a backend logged in to Kite.
- **differential-review** mentions a `differential-review:adversarial-modeler` agent that is not vendored; use `adversarial.md` in the folder instead. Write its report into the repo's `reports/` (git-ignored), not the Desktop.
- **Trail of Bits licence (CC-BY-SA-4.0)**: keep these three skills inside the repo for our own use; any changed copy we hand to others must carry the same licence and attribution.
- **Not vendored**: superpowers writing-plans (depends on unvendored skills), anthropics mcp-builder (no MCP work planned), Remotion skills (the promo toolkit does not use Remotion; Remotion has its own licence terms), tradermonty edge-strategy-reviewer / drawdown-circuit-breaker / signal-postmortem (tied to that repo's own pipeline or US data APIs).

## Updating a skill

Fetch the upstream repo at a new commit, review the diff of that folder, then ship a new fenced apply script that replaces the folder and updates this table and the lock file.
