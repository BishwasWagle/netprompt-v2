# Program-wide review — findings & backlog (2026-06-16)

Two fan-out reviews hunted latent problems. **Pass 1** (M7-weighted: regen subsystem, core loop,
deploy scripts, tests). **Pass 2** (M0–M6 gap review: monitors/hysteresis, deployer reroute/rollback,
node_runner, launch_network, kg_client, contracts, proposers). The deeper Pass 2 found real bugs in
the core — a monitor crash, a dead tune tier, reroute/rollback hazards — correcting Pass 1's
premature "safety core clean".

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

---

# Pass 2 — M0–M6 gap review (2026-06-16)

## Fixed

| Sev | Where | Fix |
|---|---|---|
| Crit | network_monitor.py `_sample_window`/`_safe_rate` | empty counter dict (dead switch) no longer crashes observe_window (`is None` sentinel + None-tolerant rate) |
| Crit | deployer.py `_restore_tables` | diff by (table,key) not handle (handles drift across a restart) |
| Crit | deployer.py REROUTE | install destination-relay edge-MAC FIRST, then s1, then host — consistent at every prefix; partial failure leaves the working old path, not a blackhole |
| Crit | kg_client.py `read_field_requirements` | skip a field node missing a bound instead of building an Envelope with None bounds (monitor crash) |
| High | pipeline.py `parse_ping` | clamp loss ≥ 0 (duplicate replies gave negative loss → flow looked better than perfect) |
| High | pipeline.py `parse_qdisc` | normalize K/M/G(bit) rate units (a Kbit/Gbit shift was invisible → misattributed rollback) |
| High | adapt.py `_grid` | inclusive of `hi` (index-based) — the top of every knob range is now reachable |
| High | adapt.py `_propose_tune` | a `None` current knob defaults to the direction-appropriate extreme, so harm relief steps DOWN (not to the max) |
| High | deployer.py `_set_path` | `ip addr replace` (not `add`) — idempotent, no mid-sequence abort if the address exists |
| High | deployer.py `rollback` | sync `_qos` bookkeeping so it can't retain a knob the live host no longer has |
| High | deployer.py `recover_switch` | verify handle count (catch a partial CLI apply) |
| High | launch_network.py `install_rules` | verify every table_add produced a handle + scan for per-line errors (topology could come up under-configured) |
| Med | kg_client.py write_verdict/write_escalation | MERGE on (correlation_id, timestamp) — idempotent retry, no duplicate nodes |
| Med | deployer.py `_ensure_forward` | scan modify output for errors instead of trusting the in-memory mutation |
| Med | node_runner.py `run_host` | include stdout in the error message |
| Low | docs/runtime-manager-design.md | hysteresis wording: windowed K-of-M (matches the code), not strictly consecutive |

## Deferred (reviewed, with reason)

- **node_runner `_pid` PID recycling** (Med) — a cached, kill-0-valid PID could be a recycled
  unrelated process. Low probability on a stable resident topology; the fix ripples into the
  cache unit tests (anchored-pgrep + cache + re-resolve) for marginal gain. Left as-is.
- **launch_network resilience** (Med) — `P4Switch.stop()` `kill %simple_switch` job-spec is
  unreliable; SIGTERM during `net.start()`/rule-install (before the handlers register) can orphan
  switches; `wait_for_thrift` only proves the port is open. Operational-teardown nits (the external
  pkill handles teardown today); changing the resident launcher wants its own live re-test.
- **network_monitor `_switch_status`** (Med) — applies the target verdict to all switches; benign
  for this single-active-path topology (only a carrying switch consults it, and it carries the
  target). Documented in code; needs per-relay verdict only for multi-field-per-relay.
- **kg_client `read_last_good` / `jsonable`** (Med) — keyed by SFC name + lossy one-way encoding;
  not consumed by the live loop (the deployer holds its own ConfigSnapshot). Documented; a warm-
  restart/reload path would need a `from_dict` and (sfc,target_field) keying.
- **node_runner `run_host` shell-injection** (suspected High, latent) — `sh -c` with interpolated
  values; all values are config constants today (no live injection). Would need an allowlist only
  if a MAC/host/path ever becomes planner/LLM-derived.
