"""Local test doubles (implementation plan M1). The real Deployer/Monitor
(M4/M5) present the same call surface, so the engine and evaluator are
developed and tested entirely off-testbed against these.
"""
from __future__ import annotations

from runtime.contracts import Candidate, MonitorReport, PRIMARY, REGEN, REROUTE, TUNE


class FakeDeployer:
    """Mutates a tiny state dict the scenario models read.

    state = {"path": PRIMARY|BACKUP, "knobs": {knob: value}}
    """

    def __init__(self, initial_path: str = PRIMARY, initial_knobs: dict | None = None):
        self.state = {"path": initial_path, "knobs": dict(initial_knobs or {})}
        self.applied: list[Candidate] = []
        self.rollbacks = 0

    def capture(self) -> dict:
        return {"path": self.state["path"], "knobs": dict(self.state["knobs"])}

    def apply(self, cand: Candidate) -> None:
        self.applied.append(cand)
        if cand.kind == REROUTE:
            self.state["path"] = cand.params[0]
        elif cand.kind == TUNE:
            knob, value = cand.params
            self.state["knobs"][knob] = value
        elif cand.kind == REGEN:
            params = cand.params
            if len(params) == 2 and all(isinstance(p, str) for p in params):
                # Real-shaped (switch, rules_text): recorded, no simulated
                # effect — fixture models don't interpret rule text.
                return
            # Simulation regens carry explicit (key, value) effects.
            for key, value in params:
                if key == "path":
                    self.state["path"] = value
                else:
                    self.state["knobs"][key] = value

    def rollback(self, snapshot: dict) -> None:
        self.rollbacks += 1
        self.state = {"path": snapshot["path"], "knobs": dict(snapshot["knobs"])}


class ScriptedRunner:
    """Runner test double for the real Deployer (deployer.py): records every
    command and answers CLI calls via an injectable handler that returns
    canned BMv2 output."""

    def __init__(self, cli_handler=None):
        self.cli_calls: list = []      # (switch, commands_text)
        self.host_calls: list = []     # (host, command)
        self.cli_handler = cli_handler or (lambda switch, text: "")

    def run_cli(self, switch: str, text: str) -> str:
        self.cli_calls.append((switch, text))
        return self.cli_handler(switch, text)

    def run_host(self, host: str, command: str) -> str:
        self.host_calls.append((host, command))
        return ""


class FakeMonitor:
    """observe_window() = ask the scenario model what the current deployer
    state looks like. Deterministic: same state -> same report."""

    def __init__(self, model, deployer: FakeDeployer):
        self.model = model
        self.deployer = deployer

    def observe_window(self) -> MonitorReport:
        return self.model.report(self.deployer.state)

    def rebaseline(self) -> None:
        """§7.6: on commit, the current state becomes the new baseline —
        later drift is judged against what was actually committed."""
        self.model.baseline = self.model._flows(self.deployer.state)
