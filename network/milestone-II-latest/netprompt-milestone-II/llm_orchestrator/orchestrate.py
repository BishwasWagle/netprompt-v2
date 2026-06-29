from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Optional

from .artifact_checker import check_compiled_config_artifacts, raise_if_artifacts_missing
from .config import RuntimeConfig
from .history_retriever import (
    PRIORITY_MAP,
    build_historical_context,
    build_live_current_row,
    load_and_normalize_results,
)
from .kg_context import (
    Neo4jContextClient,
    build_compact_llm_topology_context,
    expand_candidate_actions_with_policy_aliases,
    fallback_candidate_actions,
)
from .llm_runner import LLMOrchestrator
from .policy_compiler import compile_llm_decision_to_experiment_config
from .prompt_builder import build_llm_input_object
from .utils import safe_json_dumps
from .validator import fallback_decision, validate_generated_decision


def infer_priority(mission_type: str, explicit_priority: Optional[str]) -> str:
    if explicit_priority:
        return explicit_priority
    return PRIORITY_MAP.get(mission_type, "medium")


def build_runtime_input_object(
    cfg: RuntimeConfig,
    mission_type: str,
    priority: str,
    bandwidth: Optional[float],
    delay_ms: Optional[float],
    loss_percent: Optional[float],
    battery_percent: Optional[float],
    observed_rtt_ms: Optional[float],
    observed_loss_percent: Optional[float],
    observed_throughput_mbps: Optional[float],
    use_fallback_candidates: bool = False,
    timings: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    # Control-plane timing (KRONOS Table VIII): when a `timings` dict is supplied,
    # the KG read side ("KG Reasoning" — the warm-cache Cypher context queries) and
    # the history/context assembly are timed separately. No-op when timings is None.
    kg_client = Neo4jContextClient(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
    _t = perf_counter()
    try:
        topology_snapshot = kg_client.get_topology_snapshot()
        compact_topology = build_compact_llm_topology_context(topology_snapshot)
        candidate_actions = kg_client.get_candidate_sfc_policy_set()
        # Close the learning loop: fold the runtime's per-SFC reliability (from its
        # Verdict/EscalationTicket history) into the decision context. Best-effort —
        # {} when there's no history or NETPROMPT_PLANNER_FEEDBACK=0.
        from .analytics import feedback_for_planner
        runtime_feedback = feedback_for_planner(kg_client.run_cypher)
    finally:
        kg_client.close()
    if timings is not None:
        timings["kg_reasoning_s"] = perf_counter() - _t

    if use_fallback_candidates or not candidate_actions:
        candidate_actions = fallback_candidate_actions()
    else:
        candidate_actions = expand_candidate_actions_with_policy_aliases(candidate_actions)

    _t = perf_counter()
    history_df = load_and_normalize_results(cfg.results_csv)
    current_row = build_live_current_row(
        mission_type=mission_type,
        priority=priority,
        bandwidth=bandwidth,
        delay_ms=delay_ms,
        loss_percent=loss_percent,
        battery_percent=battery_percent,
        topology_context=compact_topology,
        observed_rtt_ms=observed_rtt_ms,
        observed_loss_percent=observed_loss_percent,
        observed_throughput_mbps=observed_throughput_mbps,
    )
    historical_context = build_historical_context(current_row, history_df, top_k=3)
    input_object = build_llm_input_object(current_row, compact_topology, candidate_actions,
                                          historical_context, runtime_feedback)
    if timings is not None:
        timings["history_build_s"] = perf_counter() - _t
    return input_object


def run_pipeline(
    cfg: RuntimeConfig,
    input_object: Dict[str, Any],
    use_fallback_only: bool = False,
    check_artifacts: bool = True,
    timings: Optional[Dict[str, float]] = None,
    repeat_decision: int = 1,
) -> Dict[str, Any]:
    # Control-plane timing (KRONOS Table VIII): separates the one-time model load
    # from the steady-state "SFC Selection" (LLM inference) and the compile stage,
    # so the reported overhead is attributable. The returned dict carries "timings".
    # repeat_decision>1 runs the decode N times on the loaded model (run 0 = cold,
    # incl. CUDA warmup; runs 1.. = warm steady-state) — recorded in
    # timings["sfc_selection_runs_s"] so warm cost can be reported apart from warmup.
    if timings is None:
        timings = {}
    raw_model_output = None

    if use_fallback_only:
        _t = perf_counter()
        decision = fallback_decision(input_object)
        timings["sfc_selection_s"] = perf_counter() - _t
        validation = validate_generated_decision(input_object, decision)
    else:
        orchestrator = LLMOrchestrator(
            model_name=cfg.model_name,
            adapter_path=cfg.adapter_path,
            use_4bit=cfg.use_4bit,
            device_map=cfg.device_map,
            max_new_tokens=cfg.max_new_tokens,
        )
        _t = perf_counter()
        orchestrator.load()
        timings["model_load_s"] = perf_counter() - _t
        from .llm_runner import parse_model_decision

        _t = perf_counter()
        raw_model_output = orchestrator.generate_raw(input_object)
        timings["sfc_selection_s"] = perf_counter() - _t

        if repeat_decision > 1:
            # Reuse the loaded model: run 0 above is cold; these are warm steady-state.
            runs = [timings["sfc_selection_s"]]
            for _ in range(repeat_decision - 1):
                _t = perf_counter()
                orchestrator.generate_raw(input_object)
                runs.append(perf_counter() - _t)
            timings["sfc_selection_runs_s"] = runs

        decision = parse_model_decision(raw_model_output)
        validation = validate_generated_decision(input_object, decision)
        if not validation["valid"]:
            print(f"[WARN] LLM decision failed validation: {validation['errors']}")
            print("[WARN] Falling back to deterministic rule-based decision.")
            _t = perf_counter()
            decision = fallback_decision(input_object)
            timings["fallback_s"] = perf_counter() - _t
            validation = validate_generated_decision(input_object, decision)

    if not validation["valid"]:
        raise ValueError(f"Both LLM and fallback decisions failed validation: {validation['errors']}")

    _t = perf_counter()
    experiment_config = compile_llm_decision_to_experiment_config(
        decision=decision,
        input_object=input_object,
        netprompt_root=cfg.netprompt_root,
    )

    artifact_check = check_compiled_config_artifacts(experiment_config)
    if check_artifacts:
        raise_if_artifacts_missing(experiment_config)
    timings["compile_s"] = perf_counter() - _t

    return {
        "raw_model_output": raw_model_output,
        "decision": decision,
        "validation": validation,
        "experiment_config": experiment_config,
        "artifact_check": artifact_check,
        "timings": timings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run NetPrompt KG-RAG LLM orchestration.")
    parser.add_argument("--mission", required=True, help="Mission type, e.g., emergency_alert_relay")
    parser.add_argument("--priority", default=None, help="Priority class. Defaults from mission type.")
    parser.add_argument("--bandwidth", type=float, default=None, help="Configured bandwidth in Mbps")
    parser.add_argument("--delay", type=float, default=None, help="Configured delay in ms")
    parser.add_argument("--loss", type=float, default=None, help="Configured loss percent")
    parser.add_argument("--battery", type=float, default=None, help="Battery percent")
    parser.add_argument("--observed-rtt", type=float, default=None, help="Optional last observed RTT in ms")
    parser.add_argument("--observed-loss", type=float, default=None, help="Optional last observed packet loss percent")
    parser.add_argument("--observed-throughput", type=float, default=None, help="Optional last observed throughput Mbps")

    parser.add_argument("--netprompt-root", default=None)
    parser.add_argument("--neo4j-uri", default=None)
    parser.add_argument("--neo4j-user", default=None)
    parser.add_argument("--neo4j-password", default=None)
    parser.add_argument("--results-csv", default=None)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--adapter-path", default=None)
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--device-map", default=None, help="cuda:0, auto, or cpu")
    parser.add_argument("--max-new-tokens", type=int, default=None)
    # Tri-state 4-bit selector. Omit BOTH flags to defer to NETPROMPT_LLM_USE_4BIT
    # (resolved in RuntimeConfig.from_env). A plain store_true would always pass a
    # concrete bool, clobbering the env — the P100 footgun this fixes.
    parser.add_argument("--4bit", dest="use_4bit", action="store_const", const=True, default=None,
                        help="Force 4-bit quantization (bitsandbytes; needs CC>=7.5 — NOT the P100).")
    parser.add_argument("--no-4bit", dest="use_4bit", action="store_const", const=False,
                        help="Force FP16 / no bitsandbytes. Omit both to defer to NETPROMPT_LLM_USE_4BIT.")
    parser.add_argument("--fallback-only", action="store_true", help="Do not load LLM; use deterministic fallback planner")
    parser.add_argument("--fallback-candidates", action="store_true", help="Use fallback candidate actions instead of KG mappings")
    parser.add_argument("--skip-artifact-check", action="store_true")
    parser.add_argument("--save-input", action="store_true", help="Also save LLM input object next to the output config")
    parser.add_argument("--save-timings", action="store_true",
                        help="Also save the control-plane timing breakdown (Table VIII) next to the output config")
    parser.add_argument("--repeat-decision", type=int, default=1,
                        help="Run the LLM decode N times on the loaded model (warm timing; run 0 is cold) for Table VIII")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    priority = infer_priority(args.mission, args.priority)
    cfg = RuntimeConfig.from_env(
        netprompt_root=args.netprompt_root,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        model_name=args.model_name,
        adapter_path=args.adapter_path,
        results_csv=args.results_csv,
        output_dir=args.output_dir,
        device_map=args.device_map,
        max_new_tokens=args.max_new_tokens,
        use_4bit=args.use_4bit,
    )

    timings: Dict[str, float] = {}
    input_object = build_runtime_input_object(
        cfg=cfg,
        mission_type=args.mission,
        priority=priority,
        bandwidth=args.bandwidth,
        delay_ms=args.delay,
        loss_percent=args.loss,
        battery_percent=args.battery,
        observed_rtt_ms=args.observed_rtt,
        observed_loss_percent=args.observed_loss,
        observed_throughput_mbps=args.observed_throughput,
        use_fallback_candidates=args.fallback_candidates,
        timings=timings,
    )

    result = run_pipeline(
        cfg=cfg,
        input_object=input_object,
        use_fallback_only=args.fallback_only,
        check_artifacts=not args.skip_artifact_check,
        timings=timings,
        repeat_decision=args.repeat_decision,
    )

    output_path = Path(args.output or Path(cfg.output_dir) / "llm_generated_experiment_config.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _t = perf_counter()
    output_path.write_text(safe_json_dumps(result["experiment_config"]))
    timings["result_writeback_s"] = perf_counter() - _t

    if args.save_input:
        input_path = output_path.with_name(output_path.stem + "_input.json")
        input_path.write_text(safe_json_dumps(input_object))
        print(f"Saved LLM input: {input_path}")

    if args.save_timings:
        timings_path = output_path.with_name(output_path.stem + "_timings.json")
        timings_path.write_text(safe_json_dumps(timings))
        print(f"Saved timings: {timings_path}")

    print("Decision:")
    print(safe_json_dumps(result["decision"]))
    print(f"Saved compiled experiment config: {output_path}")
    print("Control-plane timings (s):")
    print(safe_json_dumps(timings))
    print("Artifact check:")
    print(safe_json_dumps(result["artifact_check"]))


if __name__ == "__main__":
    main()
