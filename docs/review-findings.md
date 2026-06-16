# Program-wide review — findings & backlog (2026-06-16)

A fan-out review (regen subsystem, core loop, deploy scripts, tests) hunted latent problems.
The safety core (gate, budget, fail-safe, commit/rollback selection) checked out. Issues clustered
in the M7 additions and test-harness robustness.

## Fixed (this pass)

| # | Where | Fix |
|---|---|---|
| 1 | test_m7_regen_live.py | **Test C false green** — targeted d10 (not measured by F1) and read the stale deployer cache. Now targets d4 (F1's ping target) + reads the live switch. Re-run reveals the engine recovers via rung-3 ROLLBACK before Tier-2 regen fires (`outcome='rollback'`), so a regen-DRIVEN recovery is not demonstrated — see docs/m7-regen-live-demo.md §7b. |
| 2 | deployer.py REGEN apply | Inspect `run_cli` output for per-line CLI errors (simple_switch_CLI exits 0 on per-line failure) and raise DeployError instead of committing a partial apply. |
| 4 | grammar.py | `gbnf` now conditions args on action (forward→port, noarg→none); docstring no longer overclaims full gate-L0 lockstep (per-table action conditioning still deferred). |
| 5 | gpu-node.env + setup_gpu_node.sh | Export `NETPROMPT_TREE_ROOT` (runtime needs it; config default points at a stale `Run-time-Manager-2` tree). |
| 6 | setup_gpu_node.sh | Preserve a customized KG password on re-run instead of clobbering it to the default. |
| 9 | llm_client.py | `_complete_lines` keeps a single complete line with no trailing newline; guard `pad_token_id` against None. |
| 10 | grammar.py + gate.py | Reject leading-zero ports (`011`) in validate and the gate (exact port-string match). |

## Backlog (pre-existing, not yet fixed — deferred by decision)

| # | Sev | Where | Issue | Suggested fix |
|---|---|---|---|---|
| 3 | High | deployer.py `_restore_tables` | Diffs **by handle**, but a regen apply re-parses fresh handles, so a rollback after a regen can mis-restore or churn delete+add (the module docstring says diff by (table,key)). | Diff by `(table, key)` like `recover_switch` does. |
| 7 | High | adapt.py `improves()` | Accepts a candidate that worsens the *target* if a satisfied neighbor's headroom rises (target excluded from headroom while violating). | Don't count headroom progress when the target's own margin regresses. |
| 8 | Med | tests (M5/M6/M7) | Setup (`kg.connect`, `_start_traffic`) runs **before** `try`, so a setup failure leaks iperf/edge-server/KG-driver; `pkill -f 'iperf …'` self-matches the `sh -c` wrapper (use the `[i]perf` bracket trick); empty `_pid` mis-targets `sudo mnexec/pkill`; iperf `Popen` children are never reaped in tests (soak reaps, tests don't). | A yield-based fixture for setup/teardown + reap; bracket-trick the pkill; guard empty `_pid`. |
| 11 | Low | setup_testbed_node.sh | p4lang apt key written **unscoped** to `trusted.gpg.d` (trusts it for all repos); a broken p4lang repo is left in `sources.list.d` on failure, poisoning later `apt-get update`. | Use `[signed-by=…]` scoping (as setup_kg_node.sh does); remove the list+key on apt failure. |
| 12 | Low | switch_control.py `restart_switch` | Ignores the rules-reinstall result → a switch can come back unconfigured and be counted as a recovery (M5 teardown relies on it with no deployer follow-up). | Check the returncode / that the rules file exists before returning True. |
| — | Low | setup_kg_node.sh `--bind-lan` | Exposes Neo4j on 0.0.0.0 with the default password (warned but not blocked). | Refuse `--bind-lan` while the password is still the default. |
| — | Low | launch_network.py | SFC→path selection via `"relay" in args.sfc` substring is brittle. | Match the SFC name exactly. |

## Suspicions worth a fixture (unconfirmed)

- transformers-cfg `GrammarConstrainedLogitsProcessor.reset()` may not fully clear parser state across
  back-to-back `generate()` calls on one client — add a two-prompt test; reconstruct the processor per
  call if residual state leaks.
- `dominates()` counts harm but ignores harm *identity* — a candidate that heals field A while newly
  harming field B keeps the count equal and passes. Confirm against the design's "never regress" intent.
