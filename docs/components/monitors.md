# Monitors (`runtime/monitors/`, `runtime/node_runner.py`)

**Subsystem:** Runtime Manager (inner loop)
**One-liner:** Passive-first network monitor (noisy) plus system liveness (sound), smoothed by K-of-M hysteresis into the `MonitorReport` the loop acts on.

## Responsibility
Owns measurement: per-field RTT/loss/throughput, per-switch status derivation, baseline capture, exogenous-shift detection, and assembly of the `MonitorReport`. It computes `ProgrammableSwitch.status` from observation, replacing the hardcoded `update_topology_state.py` table. It does NOT decide adaptation, deploy, or write to the KG directly (the loop hands the report's switch status to `KGClient`). The pure computations are separated from node I/O so the orchestration is testable off-node.

## Files
- `runtime/monitors/pipeline.py` — pure logic: `HysteresisTracker`, counters/parsers, `qdisc_shifted`, `derive_switch_status`, `summarize_window`, `assemble_report`.
- `runtime/monitors/network_monitor.py` — `NetworkMonitor` (window loop + status + report) and `NodeSampler` (real I/O).
- `runtime/monitors/system_monitor.py` — `SystemMonitor` (process/thrift liveness).
- `runtime/node_runner.py` — `NodeRunner`: host commands (e.g. ping) via `mnexec` (`run_host`), CLI over thrift (`run_cli`). (The `tc qdisc` reads run directly in the root namespace via `_root_tc` in `network_monitor.py`, not through `NodeRunner`.)

## Interface
```python
class NetworkMonitor:
    def __init__(self, sampler, requirements, target_field, correlation_id, *, k, m)
    def observe_window(self) -> MonitorReport
    def capture_baseline(self) -> None
    def rebaseline(self) -> None            # §7.6: on commit, current state becomes baseline
    def baseline_snapshot(self, correlation_id, switch_status, timestamp) -> BaselineSnapshot | None

class NodeSampler:                          # ping / counters / qdiscs / liveness
    def __init__(self, runner, host_map, *, system_monitor=None, edge_ip=config.EDGE_IP, ping_count=2)

def derive_switch_status(process_alive, thrift_reachable, carrying_traffic, path_sla_ok) -> str
def qdisc_shifted(baseline, current, rel_tol=0.25) -> bool
```

## How it works
- **Passive-first measurement (§5.2):** throughput comes from `/sys/class/net/<intf>/statistics` byte counters on the SWITCH-side veths (`s1-eth{port}`, root ns) — which the deployer never writes — so a TUNE on a drone never perturbs the measured environment. RTT/loss come from a drone-namespace `ping` via `mnexec`. Liveness is process + thrift reachability.
- **One probe = counters around a ping burst;** a window is M probes. `HysteresisTracker` is K-of-M per flow: `met` flips only on a sustained condition, so noise never triggers action (§5.6).
- **Status derivation (§5.5):** `Failed` (dead process/thrift) → `Standby` (alive, idle) → `Active` (carrying, SLA ok) / `Degraded` (carrying, SLA bad). `path_sla_ok` must be the post-hysteresis verdict and is ignored for idle switches.
- **MonitorReport (§5.4)** carries target/non-target `FlowMetrics`, `switch_status`, `system_sound`, `vs_baseline` margins, `displaced_harm`, `headroom`, `exogenous_shift`, `path_confidence`. `assemble_report` is the single builder shared by fixtures and the real monitor so they can't drift.
- **Exogenous shift (§5.7):** `qdisc_shifted` compares baseline vs current `tc` params on UNMANAGED links (drone + relay links, including `s1-eth11/12`) — a change there is exogenous by definition and tells rung-3 to reroute rather than roll back.

## Gotchas & lessons
- **Unreachable ping is a VIOLATION, not 0 ms:** `None` rtt reads as `_UNREACHABLE_RTT_MS` (10 s) and `None` loss as 100%, so a dead path is never "healthy". One bad probe is absorbed by hysteresis, never aborting the episode.
- **System-fault is narrow (`_system_sound`):** only s1 `Failed`, or BOTH relays `Failed`, is a rung-1 fault. A SINGLE relay death stays sound so the loop reaches rung-4 and reroutes to the survivor.
- An empty `{}` counter dict (dead switch) is falsy but valid — the window loop uses `is None` checks, not `or`, so it isn't mistaken for "no sample".
- Rates never raise: a missing port or degenerate `dt<=0` yields 0.0 so the loop can't crash on the exact fault it must report.

## Usage
In-loop only — not a CLI. `NodeSampler` requires the resident `launch_network.py` topology (namespaces + thrift). The loop writes `observe_window().switch_status` to the KG via `KGClient.write_switch_status`.

## See also
- `../runtime-manager-design.md` §5 (Monitor + Baseline): §5.2 passive-first, §5.4 MonitorReport, §5.5 status derivation, §5.6 hysteresis, §5.7 exogenous shift; §7.6 (rebaseline on commit).
- `../usage.md` §1.4 (representative traffic — the monitor needs flows to observe), §0/§1.2 (testbed prerequisites).
- Sibling: `deployer.md` (drives the same resident network), `kg-client.md` (`write_switch_status`, `read_field_requirements`, `write_baseline`).
