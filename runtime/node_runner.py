"""Real Runner for the on-node testbed (implementation plan M4-node).

The Deployer (deployer.py) is agnostic to *how* commands reach the switches and
host namespaces — it calls an injected runner's `run_cli` / `run_host`. Off-node,
that runner is `fakes.ScriptedRunner`; on `network-node` it is this `NodeRunner`,
which drives a resident `launch_network.py` topology:

  * run_cli(switch, text) -> simple_switch_CLI over thrift (config.THRIFT_PORTS),
    commands on stdin. (M0 spike C1: handle/dump formats verified.)
  * run_host(host, cmd)  -> `sudo mnexec -a <pid> sh -c <cmd>`, where <pid> is the
    host's Mininet namespace process. (M0 spike C3: mnexec reaches namespaces.)
    `sh -c` is required because the Deployer emits shell constructs (`|| true`,
    `2>/dev/null`) in _set_path.

The subprocess call is injected (`executor`) so command construction, error
mapping and pid caching are unit-tested off-node; the default executes for real.
"""
from __future__ import annotations

import subprocess

from runtime import config


class RunnerError(RuntimeError):
    """A node command could not be executed (process failure / timeout / a
    switch or namespace is unreachable). The Deployer turns parse-level problems
    into DeployError; this is for the layer below that."""


def _subprocess_exec(argv, stdin=None, timeout=None):
    """Default executor: run argv, capture text output. No outer shell — argv is
    a list, so there is no shell-injection surface (any shell needed is the
    explicit inner `sh -c` for host commands)."""
    return subprocess.run(argv, input=stdin, capture_output=True, text=True,
                          timeout=timeout)


class NodeRunner:
    def __init__(self, thrift_ports=None, *, sudo=True, timeout=15.0,
                 executor=None):
        self.thrift_ports = dict(thrift_ports or config.THRIFT_PORTS)
        self.sudo = sudo
        self.timeout = timeout
        self._exec = executor or _subprocess_exec
        self._pid_cache: dict[str, int] = {}

    # ---- switch tables: simple_switch_CLI over thrift ----

    def run_cli(self, switch: str, text: str) -> str:
        port = self.thrift_ports.get(switch)
        if port is None:
            raise RunnerError(f"unknown switch {switch!r}")
        argv = ["simple_switch_CLI", "--thrift-port", str(port)]
        try:
            proc = self._exec(argv, stdin=text + "\n", timeout=self.timeout)
        except subprocess.TimeoutExpired as e:
            raise RunnerError(
                f"{switch}: simple_switch_CLI timed out after {self.timeout}s") from e
        out = proc.stdout or ""
        if proc.returncode != 0:
            raise RunnerError(
                f"{switch}: simple_switch_CLI exit {proc.returncode}: "
                f"{(out + (proc.stderr or '')).strip()[:200]}")
        # thrift down: some builds still exit 0 but print a connect error
        if "Could not connect" in out:
            raise RunnerError(
                f"{switch}: thrift {port} unreachable: {out.strip()[:200]}")
        return out

    # ---- host namespaces: mnexec ----

    def run_host(self, host: str, command: str) -> str:
        pid = self._pid(host)
        argv = (["sudo"] if self.sudo else []) + \
               ["mnexec", "-a", str(pid), "sh", "-c", command]
        try:
            proc = self._exec(argv, timeout=self.timeout)
        except subprocess.TimeoutExpired as e:
            raise RunnerError(f"{host}: {command[:60]!r} timed out") from e
        if proc.returncode != 0:
            raise RunnerError(
                f"{host}: {command[:60]!r} exit {proc.returncode}: "
                f"{((proc.stdout or '') + (proc.stderr or '')).strip()[:200]}")
        return proc.stdout or ""

    # ---- namespace pid resolution (anchored so d1 != d10) ----

    def _pid(self, host: str) -> int:
        pid = self._pid_cache.get(host)
        if pid is not None and self._alive(pid):
            return pid
        proc = self._exec(["pgrep", "-f", f"mininet:{host}$"], timeout=self.timeout)
        pids = (proc.stdout or "").split()
        if not pids:
            raise RunnerError(
                f"no namespace process for {host!r} — is launch_network.py up?")
        pid = int(pids[0])
        self._pid_cache[host] = pid
        return pid

    def _alive(self, pid: int) -> bool:
        return self._exec(["kill", "-0", str(pid)], timeout=self.timeout).returncode == 0
