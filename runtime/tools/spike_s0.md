# M0 Spike — Protocol & Results

**Goal:** resolve the §10.7 unknowns on `network-node` so M4/M5's node halves are
mechanical. Budget: half a day. Everything here feeds a specific code location —
fill in the Results column and apply the listed fix if a check fails.

## Setup (terminal 1)

The repo lives at `/home/cc/Run-time-Manager-2` on network-node; the canonical
milestone-II tree is `network/milestone-II-latest/netprompt-milestone-II`
(`network/newcodes/` is a leftover duplicate — do not use it).

```bash
cd /home/cc/Run-time-Manager-2 && git checkout Run-time-Manager && git pull
TREE=/home/cc/Run-time-Manager-2/network/milestone-II-latest/netprompt-milestone-II
sudo mn -c
sudo python3 runtime/tools/launch_network.py \
    --p4-json  $TREE/compiled_p4/low_latency.json \
    --rules-dir $TREE/p4_multihop_rules \
    --sfc low_latency --scenario baseline
```

Leave it running. It installs the rules and stays resident.

## Run the scripted checks (terminal 2)

```bash
bash runtime/tools/spike_s0.sh | tee spike_s0_results.txt
```

Then do **C2** manually (instructions printed at the end of the script).

## Results

| # | Check | Feeds | Result / decision |
|---|---|---|---|
| C1 | `table_add` prints `Entry has been added with handle N`; dump shows `Dumping entry 0x…` | `deployer._HANDLE_RE`, `parse_table_dump` regexes | ✅ **PASS** — `Entry has been added with handle N` and `Dumping entry 0x…` both exactly as expected; existing regexes match. |
| C1b | `table_modify … <handle> => <args>` vs bare-args form | `deployer.modify_command` (emit the accepted form); `gate._parse_command` already accepts both | ✅ **PASS** — BOTH forms accepted (`… => 2` and bare `… 2`). `deployer.modify_command` (`=>` form) is correct; no change. |
| C2 | Three-part path flip works live; table-flip-alone breaks connectivity (negative check) | design §10.1/§10.3 — `Deployer._set_path` mechanics | ⚠️ **PASS w/ CORRECTION** — flip works but is **5-part, not 3-part** (see findings). flip gap: **1 packet (~120ms wall, <200ms data-plane)** · continuity: 34/35, 2.9% loss on the in-flight packet only · negative check: extra `0c` entries on s1/s3 are harmless, primary keeps working ✓ · counter attribution clean (primary grows ↔ backup frozen, and vice-versa). |
| C3 | `mnexec -a <pid>` (or `nsenter`) reaches host namespaces out-of-process | M5 sampler (`ping` from drone ns) + Deployer tune (`tc`) — the real Runner's `run_host` | ✅ **PASS** — `sudo mnexec -a <pid>` reaches drone/edge namespaces for `ping`, `tc qdisc show`, `arp`, `ip`. Mechanism: **`mnexec` (needs root)**. Bonus: `tc qdisc show` exposes the netem shape (`delay 15ms loss 1%`) → exogenous-shift read confirmed for M5. |
| C4 | ≥3 `simple_switch` procs; `/tmp/bmv2-*` state; stable after ≥1h | `launch_network.py` watchdog need (M6 soak) | ◐ **PASS (short)** — 3 `simple_switch` procs, all `/tmp/bmv2-*.ipc` sockets present, ~15min uptime stable across many CLI sessions + flips. **≥1h soak still to run** before M6. |
| C5 | s1 port 11 → s2, port 12 → s3; `s1-eth11/12` veths exist | `config.PORT_PRIMARY/PORT_BACKUP` confirmation | ✅ **PASS** — `s1-eth11`/`s1-eth12` exist; s1 launched with `-i 11@s1-eth11 -i 12@s1-eth12`; edge `0b` entry forwards on primary. Port map confirmed. |
| C6 | veth `/sys/class/net/*/statistics` readable, counters move with traffic, update granularity | **§5.2 decision**: veth stats (zero P4 change) vs declaring P4 counters | ✅ **PASS** — `/sys/class/net/*/statistics/{rx,tx}_bytes` readable; deltas track traffic per-path and pinned path attribution during flips (primary vs backup tx). **Decision: use veth sysfs stats (zero P4 change).** Granularity sufficient for byte/path attribution; revisit only if per-packet loss proves too coarse. |

## Decisions to carry back into the repo

1. **C1b** → if the `=>` form is rejected, change `modify_command()` in
   [runtime/deployer.py](../deployer.py) to the bare form (one line) and tighten the
   gate parser to the confirmed syntax.
2. **C6** → record the §5.2 counter-channel decision in the design doc. If veth
   granularity is too coarse for loss measurement, the fallback is declaring P4
   counters (mechanical edit + recompile — observability only).
3. **C3** → whatever mechanism works (`mnexec` vs `nsenter`) becomes the real
   Runner's `run_host` implementation in M4-node.
4. **C2** → if the flip is *not* hitless, note the gap duration; the engine's
   re-observe window already absorbs sub-second blips.
5. **C4** → if any switch died or leaked, add the watchdog to `launch_network.py`
   before the M6 soak (a dead switch is a rung-1 system fault the loop should
   handle anyway — good demo material).

## C2 finding — the reroute is a 5-part action (correction to §10.1/§10.3)

Run on 2026-06-14 against the live `low_latency` baseline topology. The doc's
"three-part action" (s1 entry + edge rebind + drone ARP) is **incomplete**; a flip
done with only those three parts gives **100% loss** even though the s1 TX counter
on the new path grows (requests leave, nothing returns). The two missing parts:

1. **New-relay-switch entry for the backup edge identity.** Under a primary SFC
   (`low_latency`), `s3` has no `forward 00:00:00:00:00:0c` entry, so frames to the
   backup edge MAC reach `s3` and are **dropped there**. The flip must
   `table_add forward_table forward 00:00:00:00:00:0c => 2` on the destination
   relay switch (s3 port 2 = edge), not just the `0c => 12` entry on s1.
2. **Re-add the edge→drone static ARP after the interface rebind.** `ip addr flush`
   on the edge interface **clears the edge's static ARP entries for the drones**,
   killing the return path. `launch_network.configure_hosts` already does this
   (its drone loop re-arps), but the abbreviated C2 snippet above omits it.

The full, verified 5-part `_set_path(primary→backup)`:
```
s1:   table_add forward_table forward 00:00:00:00:00:0c => 12   # new egress port (idempotent)
s3:   table_add forward_table forward 00:00:00:00:00:0c => 2    # NEW: relay knows backup edge identity
edge: ip addr flush dev edge-eth0; ip link set edge-eth1 address 0c;
      ip addr add 10.0.0.100/24 dev edge-eth1; ip route replace 10.0.0.0/24 dev edge-eth1 src 10.0.0.100
edge: for d in drones: arp -s 10.0.0.<d> 00:00:00:00:00:0<d>   # NEW: re-arp after flush
drone(s): arp -d 10.0.0.100; arp -s 10.0.0.100 00:00:00:00:00:0c
```
Result with all five parts: **1 in-flight packet lost, 0% steady-state loss**,
RTT shifts 55ms↔67ms (primary↔backup, matches the path delays). Sub-200ms gap —
the engine's re-observe window absorbs it. **Action for M4-node:**
`Deployer._set_path` must emit the relay-switch entry and the edge re-arp;
`config` needs the relay-switch→edge port for both paths (s2:port→edge, s3:port→edge).

## M4-readiness review (2026-06-14) — two issues to resolve before the M4 exit

Beyond the C1–C6 checklist, I exercised the exact primitives `deployer.py` will run
against the live network. Two are clean; two need design work.

**✅ `parse_table_dump` round-trips on real hardware.** Ran the *actual* deployer
parser against live `table_dump` of `forward_table` / `priority_table` /
`relay_policy_table` on all three switches. All round-trip (parsed count = dumped
count), including IPv4-keyed `priority_table` (`0a000064`→`10.0.0.100`) and
**zero-arg actions** (`set_low_latency_class - ` → `args=()`), and empty tables
(`relay_policy_table`→`[]`). ConfigSnapshot/rollback parsing is safe.

**✅ ISSUE 1 — reroute missing the relay-switch entry — FIXED (2026-06-14).**
Added `config.RELAY_EDGE = {"primary":("s2",2),"backup":("s3",2)}`; `Deployer.apply`
REROUTE now `_ensure_forward`s the edge identity on BOTH s1 (relay egress port) and
the destination relay switch (edge port). `_find_s1_forward`→`_find_forward(switch,
mac)`; new idempotent `_ensure_forward` helper. Rollback already removes the relay
entry (per-switch semantic diff). Validated live: real `Deployer.apply(REROUTE→
BACKUP)` auto-installs `s1:0c→12` + `s3:0c→2`, d4→edge 0% loss @67ms; `rollback`
removes both, restores primary @55ms. Unit suite 131 green.

**⚠️ ISSUE 2 — TUNE clobbers the scenario qdisc and is not reversible.**
Verified live: `TC_TEMPLATES['tbf_rate_mbit']` = `tc qdisc replace dev d5-eth0 root
tbf …` **replaces the root**, destroying the TCLink `htb+netem` (delay 15ms loss
1%) that carries the scenario environment (ping 55ms/20%→40ms/0%). Compounding facts:
- **Impairment is mirrored on both link ends** — host `d5-eth0` *and* switch
  `s1-eth5` carry the netem. A host-side `tbf` removes only the host (upstream)
  half; switch side survives. Either way the measured environment is corrupted.
- **Rollback can't restore it.** `rollback` ([deployer.py:224](../deployer.py)) only
  re-applies knobs *present in the baseline snapshot*; `ConfigSnapshot` has no qdisc
  capture. **No binding carries a `qos` baseline today** (grep empty), so `deploy`
  sets `self._qos[host]={}` → a TUNE knob is absent from the baseline → after
  rollback the `tbf` **persists**. The M4 exit ("rollback verified by ping
  continuity") cannot pass for any episode that took a TUNE.
- The intended model is milestone-II's `apply_sfc_queue_policy` (del root, then one
  owned qdisc). It worked there because they never rolled back to the TCLink shape.

*Decision (chosen 2026-06-14): **Model B** (= milestone-II `apply_sfc_queue_policy`).*
The target field's drone-`eth0` is wholly deployer-owned (just the knob);
`replace root` is correct because there is nothing to preserve there. The scenario
environment (delay/loss) lives on the **switch-side veths (`s1-eth{N}`)**, which
the deployer never writes and the monitor reads (M5). Baseline via **Option 1**:
bindings carry a `qos` baseline, with a per-SFC `config.SFC_QOS_BASELINE` fallback
until the planner supplies it (M-K).

**✅ ISSUE 2 — FIXED (2026-06-14).** `config.SFC_QOS_BASELINE` (LowLatency→pfifo 20,
Bandwidth→tbf 80, Energy→tbf 5, Relay→none); `deploy()` uses
`binding['qos'] or SFC_QOS_BASELINE[sfc]` so a baseline knob always exists and a
TUNE is reversible. TC-template comment documents the Model-B ownership split.
Validated live (real `Deployer` + NodeRunner): baseline installs `d4-eth0 tbf 80`;
TUNE→20 changes only the knob; **switch-side `s1-eth4` htb+netem (15ms/1%) is
unchanged during the TUNE and after rollback**; rollback restores `tbf 80`, 0% loss
throughout. Unit suite 133 green (2 new Issue-2 tests).

*Consequence for M5:* the monitor must read the field environment from the
switch-side veths (`s1-eth{N}`), not drone-`eth0` (which becomes knob-only after the
first deploy). *Known asymmetry (matches milestone-II):* only the target field's
drones become knob-owned, so their upstream access-link impairment is dropped while
non-target fields keep TCLink's bi-directional shaping.

*Deferred (Option 2, optional):* capture raw `tc qdisc show` in `ConfigSnapshot`
for belt-and-suspenders replay — only needed if something ever puts an unmanaged
qdisc on a target drone-`eth0`.

## Decisions carried back (status)

1. **C1b** → no change; `=>` form accepted. ✅
2. **C6** → veth sysfs stats chosen (zero P4 change); record in design §5.2. ✅
3. **C3** → `mnexec` is the real Runner's `run_host` mechanism (root required). ✅
4. **C2** → flip is hitless to ~1 packet, BUT is 5-part (see above) — feeds `_set_path`. ⚠️
5. **C4** → no deaths/leaks in ~15min; **run the ≥1h soak before M6**, add watchdog only if it drops. ◐

## Out of scope for the spike

No KG writes, no Runtime Manager episodes, no LLM. This is purely physical-
assumption verification; M4-node starts only after this table is filled.
