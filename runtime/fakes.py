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
            # Simulation regens carry explicit (key, value) effects.
            for key, value in cand.params:
                if key == "path":
                    self.state["path"] = value
                else:
                    self.state["knobs"][key] = value

    def rollback(self, snapshot: dict) -> None:
        self.rollbacks += 1
        self.state = {"path": snapshot["path"], "knobs": dict(snapshot["knobs"])}


class FakeMonitor:
    """observe_window() = ask the scenario model what the current deployer
    state looks like. Deterministic: same state -> same report."""

    def __init__(self, model, deployer: FakeDeployer):
        self.model = model
        self.deployer = deployer

    def observe_window(self) -> MonitorReport:
        return self.model.report(self.deployer.state)
