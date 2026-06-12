#!/bin/bash
# M0 spike — scripted checks against a LIVE persistent topology
# (design §10.7; protocol + result recording in spike_s0.md).
#
# Prereq: launch_network.py is up in another terminal (rules installed).
# Run on network-node:   bash runtime/tools/spike_s0.sh | tee spike_s0_results.txt
#
# Uses a scratch MAC no host owns, so add/modify/delete on it is harmless.
# C2 (live egress flip) briefly reroutes REAL edge traffic - expected on a
# dedicated spike network.

set -u
S1=9090
SCRATCH_MAC="00:00:00:00:00:63"
EDGE_HEX="00000000000b"
PASS=0; FAIL=0

cli() {  # cli <thrift_port> <command...>
    local port=$1; shift
    echo "$*" | simple_switch_CLI --thrift-port "$port" 2>&1
}

check() {  # check <name> <ok:0|1> <detail>
    if [ "$2" -eq 0 ]; then PASS=$((PASS+1)); echo "[PASS] $1: $3"
    else FAIL=$((FAIL+1)); echo "[FAIL] $1: $3"; fi
}

echo "=== C1: handle output format (table_add / table_dump) ==="
OUT=$(cli $S1 "table_add forward_table forward $SCRATCH_MAC => 1")
echo "$OUT" | sed 's/^/    /'
HANDLE=$(echo "$OUT" | grep -oP 'Entry has been added with handle \K\d+' | head -1)
if [ -n "${HANDLE:-}" ]; then
    check "C1-handle" 0 "parsed handle=$HANDLE (matches deployer parse_handles regex)"
else
    check "C1-handle" 1 "no 'Entry has been added with handle N' line - UPDATE deployer._HANDLE_RE"
fi
DUMP=$(cli $S1 "table_dump forward_table")
echo "$DUMP" | head -25 | sed 's/^/    /'
echo "$DUMP" | grep -q "Dumping entry 0x" \
    && check "C1-dump" 0 "'Dumping entry 0x..' present (matches parse_table_dump)" \
    || check "C1-dump" 1 "dump format differs - UPDATE deployer dump regexes"

echo ""
echo "=== C1b: table_modify syntax (with vs without '=>') ==="
if [ -n "${HANDLE:-}" ]; then
    M1=$(cli $S1 "table_modify forward_table forward $HANDLE => 2")
    echo "$M1" | sed 's/^/    arrow: /'
    M2=$(cli $S1 "table_modify forward_table forward $HANDLE 2")
    echo "$M2" | sed 's/^/    bare:  /'
    echo "$M1" | grep -qiE "error|invalid|usage" \
        && check "C1b-arrow" 1 "'=>' form REJECTED - switch deployer.modify_command to bare form" \
        || check "C1b-arrow" 0 "'=>' form accepted (deployer.modify_command is correct)"
    echo "$M2" | grep -qiE "error|invalid|usage" \
        && echo "[INFO] bare form rejected (arrow form is the one)" \
        || echo "[INFO] bare form also accepted"
    cli $S1 "table_delete forward_table $HANDLE" >/dev/null
    echo "[INFO] scratch entry deleted"
else
    check "C1b" 1 "skipped - no scratch handle from C1"
fi

echo ""
echo "=== C5: port map (s1 port 11 -> s2, port 12 -> s3) ==="
echo "$DUMP" | grep -iA3 "$EDGE_HEX" | sed 's/^/    /'
echo "[INFO] edge entry action arg above: 0x0b=11 (primary/s2), 0x0c=12 (backup/s3)"
ls /sys/class/net/s1-eth11 >/dev/null 2>&1 && ls /sys/class/net/s1-eth12 >/dev/null 2>&1 \
    && check "C5-intfs" 0 "s1-eth11 and s1-eth12 exist" \
    || check "C5-intfs" 1 "expected s1-eth11/s1-eth12 veths missing - check interface naming"

echo ""
echo "=== C6: passive veth counters (granularity + liveness) ==="
IF_LIST="s1-eth1 s1-eth11 s1-eth12"
declare -A RX1 TX1
READABLE=1
for IF in $IF_LIST; do
    if [ -r "/sys/class/net/$IF/statistics/rx_bytes" ]; then
        READABLE=0
        RX1[$IF]=$(cat "/sys/class/net/$IF/statistics/rx_bytes")
        TX1[$IF]=$(cat "/sys/class/net/$IF/statistics/tx_bytes")
    else
        echo "    $IF: statistics NOT readable"
    fi
done
sleep 2
for IF in $IF_LIST; do
    if [ -n "${RX1[$IF]:-}" ]; then
        RX2=$(cat "/sys/class/net/$IF/statistics/rx_bytes")
        TX2=$(cat "/sys/class/net/$IF/statistics/tx_bytes")
        echo "    $IF over 2s: rx ${RX1[$IF]} -> $RX2 (delta $((RX2 - RX1[$IF])))  tx ${TX1[$IF]} -> $TX2 (delta $((TX2 - TX1[$IF])))"
    fi
done
check "C6-readable" $READABLE "veth /sys counters readable (zero-P4-change channel, design 5.2)"
echo "[INFO] idle deltas above = noise floor; re-read during C2's ping for traffic deltas + granularity"

echo ""
echo "=== C3: host-namespace access out-of-process (mnexec / ip netns) ==="
D4PID=$(pgrep -f "mininet:d4" | head -1)
if [ -n "${D4PID:-}" ]; then
    echo "[INFO] d4 namespace pid=$D4PID"
    # Capture first: `if cmd | sed; then` would test sed's status, not mnexec's.
    NSOUT=$(mnexec -a "$D4PID" tc qdisc show dev d4-eth0 2>/dev/null)
    if [ -n "$NSOUT" ]; then
        echo "$NSOUT" | sed 's/^/    /'
        check "C3-mnexec" 0 "mnexec reaches d4 namespace (M5 sampler + tune mechanism confirmed)"
    else
        check "C3-mnexec" 1 "mnexec failed - try: nsenter -t $D4PID -n tc qdisc show dev d4-eth0"
    fi
else
    check "C3-pid" 1 "no 'mininet:d4' process - is launch_network.py up?"
fi

echo ""
echo "=== C4: BMv2 process state + ipc files ==="
N=$(pgrep -c simple_switch || true)
[ "${N:-0}" -ge 3 ] && check "C4-procs" 0 "$N simple_switch processes" \
                    || check "C4-procs" 1 "expected >=3 simple_switch processes, found ${N:-0}"
ls -la /tmp/bmv2-* 2>/dev/null | sed 's/^/    /'
ps -o pid,etime,cmd -C simple_switch 2>/dev/null | sed 's/^/    /'
echo "[INFO] long-run stability: re-run this script after >=1h uptime and compare"

echo ""
echo "=== C2: live egress flip (MANUAL - two terminals) ==="
cat <<'EOF'
    1. Terminal A:  D4PID=$(pgrep -f "mininet:d4" | head -1)
                    mnexec -a $D4PID ping 10.0.0.100
    2. Terminal B:  find the edge-entry handle:
                      echo "table_dump forward_table" | simple_switch_CLI --thrift-port 9090
                    flip to backup, watch terminal A for continuity/RTT change:
                      echo "table_modify forward_table forward <H> => 12" | simple_switch_CLI --thrift-port 9090
                    revert:
                      echo "table_modify forward_table forward <H> => 11" | simple_switch_CLI --thrift-port 9090
    3. While pinging, re-read counters to confirm path attribution:
                      cat /sys/class/net/s1-eth11/statistics/tx_bytes   (grows on primary)
                      cat /sys/class/net/s1-eth12/statistics/tx_bytes   (grows on backup)
    Record in spike_s0.md: flip latency, ping continuity, counter attribution.
EOF

echo ""
echo "================ SUMMARY: $PASS pass, $FAIL fail ================"
echo "Record all outputs + decisions in runtime/tools/spike_s0.md"
