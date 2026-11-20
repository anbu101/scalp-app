# Vendored skills — SKILLS_PACK_20261117

Open-source agent skills copied into this repo, pinned to one upstream commit each.
Installed by `apply_skills_pack.py` (fence `SKILLS_PACK_20261117`). QA Register: TD-036, TC-SKILLS-0001…0012.
`skills-pack.lock.json` holds the sha256 of every installed file; `python3 apply_skills_pack.py --verify` checks them.
Vercel UI skills added by `apply_skills_vercel.py` (fence `SKILLS_VERCEL_20261120`); their hashes are in `skills-vercel.lock.json`, and `python3 apply_skills_vercel.py --verify` checks them.

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
| react-best-practices | vercel-labs/agent-skills | 1a2c79d4e6c4 | skills/react-best-practices | MIT (UPSTREAM-LICENSE.md in folder; upstream repo has no LICENSE file) | `UPSTREAM-LICENSE.md` added |
| web-design-guidelines | vercel-labs/agent-skills | 1a2c79d4e6c4 | skills/web-design-guidelines | MIT (LICENSE in folder, from vercel-labs/web-interface-guidelines) | rules pinned in `guidelines.md` (web-interface-guidelines `command.md` @ 434b7f913646); SKILL.md reads it instead of fetching GitHub main |

## Notes for this project

- **Project doctrine wins.** Where a skill and `doctrine-and-workflow` differ (apply scripts, fences, dual tree, falsify-first), follow the project rules.
- **backtest-expert** is written for US stocks. Its DEPLOY/REFINE thresholds are generic; for our options strategies the falsify-first rule (per-year sign tests, leave-one-year-out, walk-forward) is the gate. `scripts/evaluate_backtest.py` is stdlib-only and writes to `--output-dir` only.
- **webapp-testing**: `scripts/with_server.py` starts servers with `shell=True`. Point it at the frontend build + a mocked backend only — never at a backend logged in to Kite.
- **differential-review** mentions a `differential-review:adversarial-modeler` agent that is not vendored; use `adversarial.md` in the folder instead. Write its report into the repo's `reports/` (git-ignored), not the Desktop.
- **Trail of Bits licence (CC-BY-SA-4.0)**: keep these three skills inside the repo for our own use; any changed copy we hand to others must carry the same licence and attribution.
- **react-best-practices** (SKILLS_VERCEL_20261120) is written for Next.js. Our frontend is React 18 (Create React App, webpack) in Tauri, with no server rendering: use the `rerender-`, `rendering-`, `js-`, `client-` and most `bundle-` rules (live ticks make re-renders the main cost); skip `server-`, `async-api-routes`, `async-suspense-boundaries`, `bundle-dynamic-imports` (next/dynamic), hydration rules, and anything that needs React 19 (`rendering-activity`, `useEffectEvent`).
- **web-design-guidelines** (SKILLS_VERCEL_20261120): review pass, findings as `file:line`. Desktop app, so skip URL-state/deep-link, safe-area, touch and hydration rules, and the Title Case copy rule. The `tabular-nums`, focus, `transition: all`, virtualise-long-lists and `Intl.NumberFormat` rules do apply. To update the rules, re-pin `guidelines.md` from a new upstream commit with a new apply script.
- **Not vendored**: superpowers writing-plans (depends on unvendored skills), anthropics mcp-builder (no MCP work planned), Remotion skills (the promo toolkit does not use Remotion; Remotion has its own licence terms), tradermonty edge-strategy-reviewer / drawdown-circuit-breaker / signal-postmortem (tied to that repo's own pipeline or US data APIs).

## Updating a skill

Fetch the upstream repo at a new commit, review the diff of that folder, then ship a new fenced apply script that replaces the folder and updates this table and the lock file.
