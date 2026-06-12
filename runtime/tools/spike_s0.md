# M0 Spike — Protocol & Results

**Goal:** resolve the §10.7 unknowns on `network-node` so M4/M5's node halves are
mechanical. Budget: half a day. Everything here feeds a specific code location —
fill in the Results column and apply the listed fix if a check fails.

## Setup (terminal 1)

```bash
cd ~/netprompt-v2 && git checkout Run-time-Manager
sudo mn -c
sudo python3 runtime/tools/launch_network.py \
    --p4-json  /home/cc/netprompt-milestone-II/compiled_p4/low_latency.json \
    --rules-dir /home/cc/netprompt-milestone-II/p4_multihop_rules \
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
| C1 | `table_add` prints `Entry has been added with handle N`; dump shows `Dumping entry 0x…` | `deployer._HANDLE_RE`, `parse_table_dump` regexes | ☐ |
| C1b | `table_modify … <handle> => <args>` vs bare-args form | `deployer.modify_command` (emit the accepted form); `gate._parse_command` already accepts both | ☐ which form: |
| C2 | Edge-entry flip 11↔12 takes effect immediately for an in-flight ping | design §10.3 (live re-install, no teardown) | ☐ flip latency: · continuity: |
| C3 | `mnexec -a <pid>` (or `nsenter`) reaches host namespaces out-of-process | M5 sampler (`ping` from drone ns) + Deployer tune (`tc`) — the real Runner's `run_host` | ☐ mechanism: |
| C4 | ≥3 `simple_switch` procs; `/tmp/bmv2-*` state; stable after ≥1h | `launch_network.py` watchdog need (M6 soak) | ☐ uptime checked: |
| C5 | s1 port 11 → s2, port 12 → s3; `s1-eth11/12` veths exist | `config.PORT_PRIMARY/PORT_BACKUP` confirmation | ☐ |
| C6 | veth `/sys/class/net/*/statistics` readable, counters move with traffic, update granularity | **§5.2 decision**: veth stats (zero P4 change) vs declaring P4 counters | ☐ granularity: · decision: |

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

## Out of scope for the spike

No KG writes, no Runtime Manager episodes, no LLM. This is purely physical-
assumption verification; M4-node starts only after this table is filled.
