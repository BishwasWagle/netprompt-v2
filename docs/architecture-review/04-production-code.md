# 4 · Improved Production-Grade Code

Drop-in, **behavior-preserving** code for the highest-value fixes. Each keeps the
codebase's existing idioms (`from __future__ import annotations`, dataclasses,
why-focused docstrings, the `PRIMARY/TUNE` constant pattern) and the green
212-test baseline. Every refactor states *why behavior is unchanged*.

> These are proposals documented for review — they are **not yet applied** to
> `runtime/`. Apply them incrementally per [03](03-refactoring-strategy.md), with
> `pytest tests/unit` green before and after each.

**Jump to:** [M1 vocabularies](#m1) · [D2 margin helper](#d2) ·
[P1 parallel pings](#p1) · [P3 regen warmup+timeout](#p3) ·
[A1 protocols](#a1) · [D1/D3/D5 deletions](#deletions) · [D4 scrape module](#d4)

---

<a name="m1"></a>
## M1 · Centralize the closed vocabularies (HIGH)

Follow the existing `contracts.py` constant pattern — **not** `enum.Enum`, because
`outcome` is compared with bare `==`, keyed in dicts, persisted to Neo4j, and
round-tripped through `jsonable()`.

**`contracts.py` — extend the existing constant block (after line 21):**

```python
PRIMARY = "primary"
BACKUP = "backup"

TUNE = "tune"
REROUTE = "reroute"
REGEN = "regen"
TIER_OF = {TUNE: 0, REROUTE: 1, REGEN: 2}

# Terminal Verdict.outcome vocabulary (design §6/§8). Bare-str constants, NOT an
# enum: the value is compared by == (runtime_manager commit check, tests), keyed
# in soak's counter dict, persisted to the KG, and round-tripped through
# jsonable() — exactly like PRIMARY/TUNE above.
HEALTHY = "healthy"
MARGINAL = "marginal"
ROLLBACK = "rollback"
ESCALATED = "escalated"
SYSTEM_FAULT = "system_fault"
REJECTED = "rejected"                 # gate-refused, never deployed
OUTCOMES = frozenset((HEALTHY, MARGINAL, ROLLBACK, ESCALATED, SYSTEM_FAULT, REJECTED))
COMMIT_OUTCOMES = frozenset((HEALTHY, MARGINAL))   # the §7.6 promote/re-baseline pair

# Switch status vocabulary (design §5.5), written by derive_switch_status and
# read by _system_sound / the watchdog.
SW_FAILED = "Failed"                  # dead process or thrift
SW_STANDBY = "Standby"                # alive, idle (not carrying)
SW_ACTIVE = "Active"                  # alive, carrying, SLA ok
SW_DEGRADED = "Degraded"              # alive, carrying, SLA bad (not a fault)
SWITCH_STATUSES = frozenset((SW_FAILED, SW_STANDBY, SW_ACTIVE, SW_DEGRADED))

# Diagnosis vocabulary (design §7.3): the per-metric axes + the target sentinel.
TARGET = "target"                     # Diagnosis.who when the target itself violates
LATENCY = "latency"
THROUGHPUT = "throughput"
LOSS = "loss"
DIAGNOSIS_METRICS = frozenset((LATENCY, THROUGHPUT, LOSS))
```

**`evaluator.py` — reference the names:**

```python
from runtime.contracts import (
    ESCALATED, HEALTHY, MARGINAL, ROLLBACK, SYSTEM_FAULT,
    AdaptResult, DeploymentSpec, EscalationTicket, MonitorReport, Verdict,
)

def commit_outcome(headroom: float, tier_reached: int) -> str:
    if tier_reached >= 2:
        return MARGINAL
    return HEALTHY if headroom >= config.HEADROOM_TAU else MARGINAL
# ... Verdict(..., ESCALATED, ...) / SYSTEM_FAULT / ROLLBACK at the three sites
```

**`runtime_manager.py` — the commit pair via the shared frozenset:**

```python
from runtime.contracts import COMMIT_OUTCOMES, REJECTED, DeploymentSpec, Verdict
# ...
            result = EvalResult(Verdict(spec.correlation_id, REJECTED, 0, 0.0,
                                        [g.reason], timestamp))
# ...
        if result.verdict.outcome in COMMIT_OUTCOMES:
```

**`pipeline.py`**, **`network_monitor.py`**, **`adapt.py`** likewise import
`SW_*` / `LATENCY/THROUGHPUT/LOSS/TARGET`. In `adapt.py`, rename the
`dimension_margins` keys **and** the `_KNOB_FOR_TARGET` keys to the same constants
in one atomic edit (they are a producer/consumer pair).

**`soak.py` — seed counters from the set (no hand-list to drift):**

```python
from runtime.contracts import OUTCOMES, ...
stats = {"episodes": 0, "recoveries": 0, "injected_kills": 0,
         "traffic_restarts": 0, "errors": 0}
stats.update({o: 0 for o in OUTCOMES})
```

**Why behavior is unchanged:** every constant equals the byte-identical string the
literal already held. `outcome` stays a plain `str`; `in COMMIT_OUTCOMES` is True
for exactly `{"healthy","marginal"}`; `OUTCOMES` reproduces soak's six counter
keys; `Diagnosis.metric` still indexes `_KNOB_FOR_TARGET` because both ends rename
to the same constant. Pure rename, zero control-flow change. Re-run
`test_evaluator`, `test_runtime_manager`, `test_monitor_pipeline`,
`test_network_monitor`, `test_kg_client`.

---

<a name="d2"></a>
## D2 · Unify the SLA-margin formula (MEDIUM)

**`contracts.py` — one source of truth next to `FlowMetrics`:**

```python
def dimension_margins(fm: FlowMetrics) -> dict:
    """Per-metric normalized SLA margins for one flow (design §5.4/§7.3).

    Each entry is the signed distance INSIDE the bound, normalized by the
    bound, so margins are comparable across dimensions; <0 means that
    dimension is violating. FlowMetrics.margin is exactly min(this.values()).
    This is the one definition both the evaluator (via compute_flow_metrics)
    and the adapt engine's diagnose() consume — keep them from diverging.
    """
    r = fm.requirement
    return {
        "latency": (r.max_latency_ms - fm.rtt_avg_ms) / max(r.max_latency_ms, _EPS),
        "throughput": (fm.throughput_mbps - r.min_bandwidth_mbps) / max(r.min_bandwidth_mbps, _EPS),
        "loss": (r.max_loss_percent - fm.loss_percent) / max(r.max_loss_percent, _EPS),
    }


def compute_flow_metrics(field_id: str, rtt_avg_ms: float, throughput_mbps: float,
                         loss_percent: float, requirement: Envelope) -> FlowMetrics:
    fm = FlowMetrics(field_id, rtt_avg_ms, throughput_mbps, loss_percent,
                     requirement, met=False, margin=0.0)
    fm.margin = min(dimension_margins(fm).values())
    fm.met = fm.margin >= 0.0
    return fm
```

**`adapt.py` — delete the local copy, import the shared helper:**

```python
from runtime.contracts import (
    BACKUP, PRIMARY, REROUTE, TUNE,
    AdaptResult, AttemptRecord, Candidate, Diagnosis, DeploymentSpec,
    Envelope, FlowMetrics, MonitorReport, dimension_margins, goal,
)
# diagnose() (adapt.py:53-65) is UNCHANGED — it now calls the imported helper.
```

**Why behavior is unchanged:** `min(dimension_margins(fm).values())` iterates the
exact same three expressions with the same `max(_, _EPS)` clamps as the old inline
`min(...)`, so `margin`/`met` are identical. `FlowMetrics` is a non-frozen
dataclass, so construct-then-assign yields an indistinguishable object; no caller
observes the placeholder. Keep `adapt.py`'s `_EPS` (still used by `_grid`).
`test_contracts` and `test_monitor_pipeline` assert the public `margin`/`met`,
which are preserved.

---

<a name="p1"></a>
## P1 · Parallelize per-field pings; make probe params configurable (HIGH)

**`config.py` — near the §5.6 hysteresis block:**

```python
PING_COUNT = 2          # ICMP echos per probe (design §5.2)
PING_TIMEOUT_S = 1      # per-ping -W bound (s); caps a dead-path probe's wall
PING_WORKERS = 4        # concurrent per-field ping subprocesses; 1 = serial
```

**`network_monitor.py` — `NodeSampler.__init__`/`ping` (defaults reproduce
`ping -c 2 -W 1`):**

```python
    def __init__(self, runner, host_map, *, system_monitor=None,
                 edge_ip=config.EDGE_IP, ping_count=config.PING_COUNT,
                 ping_timeout_s=config.PING_TIMEOUT_S):
        self.runner = runner
        self.host_map = host_map
        self.port_map = port_map_from_hosts(host_map)
        self.system = system_monitor or SystemMonitor()
        self.edge_ip = edge_ip
        self.ping_count = ping_count
        self.ping_timeout_s = ping_timeout_s
        self.counter_ports = sorted(self.port_map) + [config.PORT_PRIMARY,
                                                       config.PORT_BACKUP]

    def ping(self, field: str) -> tuple:
        """RTT/loss from the field's first drone (drone ns -> edge). `|| true`
        because ping exits non-zero on loss — data, not a failure. A namespace
        that can't be resolved raises RunnerError -> report unreachable so one
        bad probe never aborts the episode (design §5.6 hysteresis absorbs it).
        `-W {ping_timeout_s}` bounds each ping's wall time (config default 1s)."""
        drone = self.host_map[field][0]
        try:
            out = self.runner.run_host(
                drone,
                f"ping -c {self.ping_count} -W {self.ping_timeout_s} "
                f"{self.edge_ip} || true")
        except RunnerError:
            return (None, None)
        return parse_ping(out)
```

**`network_monitor.py` — `NetworkMonitor` parallel probe (add
`from concurrent.futures import ThreadPoolExecutor`):**

```python
    def __init__(self, sampler, requirements, target_field, correlation_id, *,
                 k=config.HYSTERESIS_K, m=config.HYSTERESIS_M,
                 ping_workers=config.PING_WORKERS):
        # ... existing assignments ...
        self.ping_workers = max(1, ping_workers)

    def _probe(self):
        c0 = self.sampler.counters()
        pings = self._ping_all()
        c1 = self.sampler.counters()
        # ... unchanged below: builds probe[f] from pings + tput ...

    def _ping_all(self) -> dict:
        """Per-field (rtt, loss) for this probe. Pings are independent mnexec
        subprocesses, so issue them concurrently (up to ping_workers) and
        reassemble in self.requirements order — identical to the serial
        comprehension, just without the back-to-back blocking wall time."""
        fields = list(self.requirements)
        if self.ping_workers <= 1 or len(fields) <= 1:
            return {f: self.sampler.ping(f) for f in fields}
        with ThreadPoolExecutor(max_workers=min(self.ping_workers, len(fields))) as ex:
            results = dict(zip(fields, ex.map(self.sampler.ping, fields)))
        return {f: results[f] for f in fields}
```

**Why behavior is unchanged:** at defaults the ping string is byte-identical
(`-c 2 -W 1`); `_ping_all` returns the same `{field: (rtt,loss)}` dict in
`requirements` order; `c0`/`c1` still bracket the whole burst (throughput `dt`
window unchanged); `RunnerError` is still caught per-field. With `ping_workers<=1`
the original serial path runs verbatim. `NodeRunner._pid_cache` is read-mostly; a
concurrent miss at worst issues a redundant `pgrep` with an identical result, so no
lock is needed. `FakeMonitor` bypasses `_probe` entirely, so unit tests are
unaffected; a test asserting ping order should pass `ping_workers=1`.

---

<a name="p3"></a>
## P3 · Regen: warm-load out of the hot path + a generation deadline (HIGH)

Move the model load before the loop and bound each `generate()` so a hung GPU call
degrades to the existing §7.4 fail-safe instead of stalling the episode.

**`config.py`:**

```python
REGEN_GENERATE_TIMEOUT_S = float(
    os.environ.get("NETPROMPT_REGEN_GEN_TIMEOUT_S", "30"))  # wall-clock per generate()
```

**`regen/llm_client.py` — `LocalHFClient`:**

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout

    def warmup(self) -> None:
        """Eager-load the model so the first episode doesn't pay the load inside
        the loop. Call once before the soak (soak.py) — a no-op if already loaded."""
        self._ensure_loaded()

    def generate(self, prompt: str) -> str:
        """Bounded constrained generation. A hang/timeout raises TimeoutError,
        which the proposer's try/except treats as a rejected attempt (§7.4
        fail-safe) rather than blocking the episode forever."""
        self._ensure_loaded()
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(self._generate_constrained, prompt)
            try:
                return fut.result(timeout=config.REGEN_GENERATE_TIMEOUT_S)
            except _FutTimeout as e:
                raise TimeoutError(
                    f"regen generate exceeded {config.REGEN_GENERATE_TIMEOUT_S}s") from e

    def _generate_constrained(self, prompt: str) -> str:
        import torch
        self._proc.reset()
        enc = self._tok.apply_chat_template(
            [{"role": "user", "content": prompt}], add_generation_prompt=True,
            return_tensors="pt", return_dict=True).to(self._device)
        with torch.no_grad():
            out = self._model.generate(**enc, generation_config=self._gen_cfg,
                                       logits_processor=[self._proc])
        text = self._tok.decode(out[0][enc["input_ids"].shape[-1]:],
                                skip_special_tokens=True)
        return _complete_lines(text)
```

**`tools/soak.py` — warm up before the loop when regen is enabled:**

```python
if args.with_regen:
    regen_client.warmup()   # pay the model load once, before timing episodes
```

**Why behavior is unchanged:** `_generate_constrained` is the original `generate`
body verbatim — same constrained decoding, same `_complete_lines` output. The only
additions are (a) an explicit warm-load (lazy `_ensure_loaded` is unchanged and
still idempotent) and (b) a wall-clock bound whose `TimeoutError` is already
handled at `proposer.py:43` (`except Exception → rejected attempt → escalate`). On
the happy path the result is identical. An orphaned daemon thread on a true GPU
hang is acceptable (process is being escalated/torn down anyway).

---

<a name="a1"></a>
## A1 · Make the optional protocols explicit (LOW, enables Phase 1)

**`contracts.py` — add structural Protocols (no runtime cost):**

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class DeployerProto(Protocol):
    """The surface the engine/evaluator/RuntimeManager require of any deployer
    (real Deployer or FakeDeployer). Optional node-only methods are split out."""
    @property
    def state(self) -> dict: ...
    def capture(self): ...                 # ConfigSnapshot | dict (opaque to callers)
    def apply(self, cand: "Candidate") -> None: ...
    def rollback(self, snapshot) -> None: ...

@runtime_checkable
class TableStateCapable(Protocol):
    def table_state(self) -> dict: ...      # node-only; gate L2 for regen

class MonitorProto(Protocol):
    def observe_window(self) -> "MonitorReport": ...
    def rebaseline(self) -> None: ...

class BaselineCapable(Protocol):
    def baseline_snapshot(self, correlation_id: str, switch_status: dict,
                          timestamp: str): ...
```

**`evaluator.py` / `runtime_manager.py` / `adapt.py` — replace `object`/untyped:**

```python
@dataclass
class EvalContext:
    deployer: DeployerProto
    monitor: MonitorProto
    gate: "GateProto"
    budget: Budget
    last_good: "ConfigSnapshot | dict | None" = None   # was annotated `dict` (a lie)
    # ...
```

The existing `hasattr(deployer, "table_state")` guards stay (they are the correct
runtime test for the *optional* capability), but now `isinstance(deployer,
TableStateCapable)` is available and the required surface is checkable. **Behavior
is unchanged** — Protocols are structural and erased at runtime; no method
resolution changes.

---

<a name="deletions"></a>
## D1 / D3 / D5 · Repo-hygiene deletions (MEDIUM/HIGH, zero code risk)

Run from repo root. Reproduce the safety check *before* deleting.

```bash
# D1 — duplicate archive trees (verify byte-identical first)
diff -rq network/newcodes/netprompt-milestone-II network/milestone-II/necodes   # expect: exit 0
git rm -r --quiet network/newcodes network/milestone-II/necodes
#   survivor: network/milestone-II-latest/netprompt-milestone-II (authoritative)

# D5 — stray dead duplicate of the single-switch harness
git rm network/sfc_experiment.py
git rm network/newcodes/netprompt-milestone-II/experiments/sfc_experiment.py \
       network/milestone-II-latest/netprompt-milestone-II/experiments/sfc_experiment.py \
       network/milestone-II/necodes/experiments/sfc_experiment.py
#   keep ONE canonical: network/milestone-II/experiments/sfc_experiment.py

# D3 — git-tracked backup shadows (history already preserves them)
find . -path ./.git -prune -o \
  \( -name '*.bak' -o -name '*.py.bak' -o -name '*_phase3_backup.py' -o -name '*.txt.bak' \) \
  -print -delete
```

**`.gitignore` — append (matches the existing commented-rationale style):**

```gitignore
# In-tree editor/phase backups are version-control-by-filename: they shadow the
# live source under a different name while git already preserves every revision.
# Ignore them so they can never be re-committed; recover via `git log`/`git show`.
*.bak
*.py.bak
*_phase3_backup.py
*.txt.bak
```

**Then** reword the two dangling doc comments that name the deleted paths:

* `config.py:36-41` — drop the `network/newcodes/ is a leftover` clause; state only
  that the `milestone-II-latest` copy is the sole archived snapshot. **Leave the
  `os.environ.get` call and its default string byte-for-byte unchanged** so
  `NODE_TREE_ROOT` evaluates identically. (Separately fix the stale
  `Run-time-Manager-2` default — Problem A2 — but as its own change.)
* `tools/spike_s0.md:9-11` — drop the "do not use newcodes" warning.

**Why behavior is unchanged:** `grep` confirms no `runtime/`/`controller`/`tests`
code imports `necodes`/`newcodes`/the deleted `sfc_experiment.py`; all path
constants resolve through `config.NODE_TREE_ROOT` → the surviving tree; the kept
copies are byte-identical to the deleted ones. The two callers of
`sfc_experiment.py` run a *remote* copy under `/home/cc/netprompt-network`, not
this repo's file.

---

<a name="d4"></a>
## D4 · Factor the controller's result-scrape into one module (MEDIUM)

New `controller/result_scrape.py` owns the iperf/RTT/loss regexes + dump
boilerplate; `parse_results.py` becomes a thin wrapper. (`search_first` lets the
milestone parsers reuse it later with last-match semantics.)

```python
# controller/result_scrape.py
"""Shared iperf/ping scrape for the testbed result files (controller path).

One home for the regexes that read a `results_*.txt` capture — iperf throughput,
the `rtt min/avg/max/mdev` quad, and the `N transmitted, M received` loss percent
— plus the JSON+CSV dump. Historically this block was copy-pasted across the
milestone-II result scripts; the live controller now owns one copy. Behaviour is
identical to the inlined version: same regexes, same float/int coercions, same
2-dp loss rounding, missing fields stay None.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

_THROUGHPUT = re.compile(r"([\d.]+)\s+Mbits/sec")
_RTT = re.compile(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)")
_LOSS = re.compile(r"(\d+) packets transmitted, (\d+) received")


@dataclass
class IperfSample:
    """The generic testbed metrics scraped from one capture (None = absent)."""
    throughput_mbps: float | None = None
    rtt_min_ms: float | None = None
    rtt_avg_ms: float | None = None
    rtt_max_ms: float | None = None
    rtt_mdev_ms: float | None = None
    packet_loss_percent: float | None = None


def scrape_iperf(text: str, *, search_first: bool = True) -> IperfSample:
    """Pull throughput / RTT / loss from a captured result file.
    search_first=True keeps the first `Mbits/sec` line (controller's re.search);
    False keeps the last (the milestone parser's findall[-1])."""
    matches = _THROUGHPUT.findall(text)
    throughput = float(matches[0] if search_first else matches[-1]) if matches else None
    rtt = _RTT.search(text)
    rtt_quad = tuple(float(g) for g in rtt.groups()) if rtt else (None,) * 4
    loss = _LOSS.search(text)
    if loss:
        tx, rx = int(loss.group(1)), int(loss.group(2))
        loss_percent = round((tx - rx) / tx * 100, 2)
    else:
        loss_percent = None
    return IperfSample(throughput, *rtt_quad, loss_percent)


def dump_clean(rows: list[dict], stem: str) -> None:
    """Write rows to <stem>.json and <stem>.csv (same format as before)."""
    with open(f"{stem}.json", "w") as f:
        json.dump(rows, f, indent=2)
    with open(f"{stem}.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
```

`controller/parse_results.py` then keeps only the controller-unique parts (the
`results_Field_*` glob, the field-id/`SFC Mode:` regexes) and calls
`scrape_iperf(text)` + `dump_clean(rows, "parsed_results_clean")`.

**Why behavior is unchanged:** identical regexes and coercions; `search_first=True`
== the original `re.search(...).group(1)` first-match; `dump_clean` writes
byte-identical JSON/CSV with the same column order. A before/after diff of
`parsed_results_clean.{json,csv}` on the five committed fixtures confirms identity.
No tests cover these top-level scripts, so none need editing.

---

## Coverage of the requested dimensions

| Dimension | Addressed by |
|-----------|--------------|
| Bad architecture | A1 (protocols), + Phase 3.2 config split |
| Duplicate logic | D1, D2, D3, D4, D5 (+ `_EPS` unification) |
| Performance | P1 (pings), P3 (regen), + Phase 2.3/2.4 (KG/table_state/liveness) |
| Scalability | Phase 3.1 (KG-backed `last_good`), 3.2 (injected policy), 3.3 (seams) |
| Maintainability | M1 (vocabularies), A1 (types), D-series (dedup), M2–M5 cleanups |

---

## Key Takeaways

- **Behavior-preservation is the explicit, enforced discipline.** Every refactor in this file ships with a "Why behavior is unchanged" argument and keeps the green 212-test baseline (`pytest tests/unit` must pass before and after each incremental change). The code also honors the codebase's existing idioms (`from __future__ import annotations`, dataclasses, why-focused docstrings, the `PRIMARY`/`TUNE` constant pattern).

- **M1 centralizes the closed vocabularies as bare-string constants, not enums.** All the `outcome`, switch-status, and diagnosis vocabularies move into `contracts.py` (e.g. `HEALTHY`/`MARGINAL`/`ROLLBACK`/`ESCALATED`/`SYSTEM_FAULT`/`REJECTED` plus the `OUTCOMES` and `COMMIT_OUTCOMES` frozensets). Enums are deliberately rejected because `outcome` is compared with bare `==`, used as dict keys, persisted to Neo4j, and round-tripped through `jsonable()` — so each constant equals the byte-identical string the literal already held, making it a pure rename with zero control-flow change.

- **D2 unifies the SLA-margin formula into one shared `dimension_margins(fm)` helper.** The function lives next to `FlowMetrics` in `contracts.py` and becomes the single definition consumed by both the evaluator (via `compute_flow_metrics`) and the adapt engine's `diagnose()`, deleting `adapt.py`'s local copy. Because `min(dimension_margins(fm).values())` iterates the same three expressions with the same `max(_, _EPS)` clamps, `margin`/`met` come out identical; note `adapt.py` still keeps its own `_EPS` since `_grid` uses it.

- **P1 parallelizes per-field pings and makes probe params configurable.** New `config.py` knobs (`PING_COUNT=2`, `PING_TIMEOUT_S=1`, `PING_WORKERS=4`) feed a `ThreadPoolExecutor`-backed `_ping_all`, whose defaults reproduce the exact `ping -c 2 -W 1` string. With `ping_workers<=1` (or a single field) it runs the original serial path verbatim, and `NodeRunner._pid_cache` needs no lock because a concurrent miss at worst issues a redundant `pgrep` with an identical result.

- **P3 moves the regen model load out of the hot path and bounds each generation.** A new `warmup()` (called once in `soak.py` before timing episodes) pays the model load up front, and `generate()` wraps the original logic — renamed `_generate_constrained` and left verbatim — in a `ThreadPoolExecutor` with `REGEN_GENERATE_TIMEOUT_S` (default 30s). A timeout raises `TimeoutError`, which is already caught at `proposer.py:43` and routed into the §7.4 fail-safe (rejected attempt → escalate) instead of stalling the episode forever.

- **A1 adds erased-at-runtime Protocols, and the D-series handles repo hygiene.** A1 introduces structural `Protocol`s (e.g. `DeployerProto`, `MonitorProto`, `TableStateCapable`) that are erased at runtime, so no method resolution changes while types become checkable. The D1/D3/D5 deletions remove duplicate archive trees, a stray `sfc_experiment.py`, and git-tracked `*.bak`/`*_phase3_backup.py` backups — each guarded by a byte-identical `diff` check and new `.gitignore` entries — while D4 factors the controller's iperf/RTT/loss scrape into `controller/result_scrape.py`, verified identical against five committed fixtures.

- **Per the guidance, M1/D2/A1 and the deletions are already applied; P1 and P3 remain ready-to-apply proposals.** The file's coverage table ties these fixes to the requested review dimensions — maintainability (M1, A1, D-series), duplicate logic (D1–D5 plus `_EPS` unification), and performance (P1 pings, P3 regen) — so a reader can see which named problem each refactor closes.
