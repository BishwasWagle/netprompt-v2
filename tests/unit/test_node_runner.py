"""M4-node — NodeRunner command construction + error mapping, tested off-node
with an injected fake executor. The mechanisms themselves (simple_switch_CLI
formats, mnexec namespace access, anchored pgrep) are M0-spike-verified on the
node; here we pin the argv we build and how failures map to RunnerError."""
import subprocess
import types

import pytest

from runtime.node_runner import NodeRunner, RunnerError


def result(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeExec:
    """Records every argv; answers by command. Programmable per-channel."""
    def __init__(self):
        self.calls = []
        self.pid = "12345"
        self.cli = result(0, "Entry has been added with handle 0\n")
        self.host = result(0, "host-out\n")
        self.alive = True
        self.timeout_on = None          # substring -> raise TimeoutExpired

    def __call__(self, argv, stdin=None, timeout=None):
        self.calls.append(types.SimpleNamespace(argv=argv, stdin=stdin, timeout=timeout))
        joined = " ".join(argv)
        if self.timeout_on and self.timeout_on in joined:
            raise subprocess.TimeoutExpired(argv, timeout)
        if argv[0] == "pgrep":
            return result(0, (self.pid + "\n") if self.pid else "")
        if argv[0] == "kill":
            return result(0 if self.alive else 1)
        if "simple_switch_CLI" in argv:
            return self.cli
        return self.host                # sudo/mnexec host command


def runner(**kw):
    fx = FakeExec()
    r = NodeRunner(executor=fx, **kw)
    return r, fx


# ---------------- run_cli ----------------

def test_run_cli_builds_thrift_argv_and_pipes_stdin():
    r, fx = runner()
    out = r.run_cli("s2", "table_dump forward_table")
    call = fx.calls[-1]
    assert call.argv == ["simple_switch_CLI", "--thrift-port", "9091"]
    assert call.stdin == "table_dump forward_table\n"
    assert "handle 0" in out


def test_run_cli_unknown_switch_raises():
    r, _ = runner()
    with pytest.raises(RunnerError, match="unknown switch"):
        r.run_cli("s9", "table_dump x")


def test_run_cli_nonzero_exit_raises():
    r, fx = runner()
    fx.cli = result(1, "", "boom")
    with pytest.raises(RunnerError, match="exit 1"):
        r.run_cli("s1", "x")


def test_run_cli_detects_thrift_down_on_zero_exit():
    r, fx = runner()
    fx.cli = result(0, "Could not connect to any of [('127.0.0.1', 9090)]")
    with pytest.raises(RunnerError, match="unreachable"):
        r.run_cli("s1", "x")


def test_run_cli_timeout_raises():
    r, fx = runner()
    fx.timeout_on = "simple_switch_CLI"
    with pytest.raises(RunnerError, match="timed out"):
        r.run_cli("s1", "x")


# ---------------- run_host ----------------

def test_run_host_wraps_in_mnexec_sh_c_with_sudo():
    r, fx = runner()
    out = r.run_host("d4", "arp -s 10.0.0.100 00:00:00:00:00:0c")
    call = fx.calls[-1]
    assert call.argv == ["sudo", "mnexec", "-a", "12345", "sh", "-c",
                         "arp -s 10.0.0.100 00:00:00:00:00:0c"]
    assert out == "host-out\n"


def test_run_host_no_sudo_option():
    r, fx = runner(sudo=False)
    r.run_host("edge", "ip addr flush dev edge-eth0 || true")
    assert fx.calls[-1].argv[0] == "mnexec"          # no leading sudo


def test_run_host_nonzero_exit_raises():
    r, fx = runner()
    fx.host = result(2, "", "RTNETLINK answers: File exists")
    with pytest.raises(RunnerError, match="exit 2"):
        r.run_host("edge", "ip addr add 10.0.0.100/24 dev edge-eth0")


def test_run_host_missing_namespace_raises():
    r, fx = runner()
    fx.pid = ""                                       # pgrep finds nothing
    with pytest.raises(RunnerError, match="no namespace process"):
        r.run_host("d4", "true")


# ---------------- pid resolution / caching ----------------

def test_pid_uses_anchored_pgrep_and_caches():
    r, fx = runner()
    r.run_host("d1", "true")
    r.run_host("d1", "true")
    pgreps = [c for c in fx.calls if c.argv[0] == "pgrep"]
    assert pgreps[0].argv == ["pgrep", "-f", "mininet:d1$"]   # anchored: d1 != d10
    assert len(pgreps) == 1                                   # second call cached


def test_pid_reresolves_when_cached_pid_died():
    r, fx = runner()
    r.run_host("d4", "true")
    fx.alive = False                                  # the cached pid is gone
    fx.pid = "67890"                                  # namespace re-created
    r.run_host("d4", "true")
    assert r._pid_cache["d4"] == 67890
    assert len([c for c in fx.calls if c.argv[0] == "pgrep"]) == 2
