"""Retrain the planner LoRA so the LLM picks mission-appropriate SFCs (approach #3).

The shipped adapter collapses to LowLatencyVideoSFC for every mission (prompt/few-shot
can't fix it — docs 6d). This trains a FRESH LoRA on a balanced dataset whose labels
come from the deterministic oracle (validator.fallback_decision), which already encodes
the correct mission/telemetry -> SFC policy. Same prompt format the orchestrator uses
at inference (prompt_builder.build_inference_prompt), so the new adapter is drop-in.

  source deploy/gpu-node/gpu-node.env
  ~/netprompt-venv/bin/python train_decision_lora.py \
      --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
      --out netprompt_qwen_kg_rag_orchestrator/final_adapter_retrained

The original adapter is preserved separately (final_adapter_original_backup) for
rollback/compare.
"""
from __future__ import annotations

import argparse
import json
import random

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

from llm_orchestrator.kg_context import (
    Neo4jContextClient, build_compact_llm_topology_context,
    expand_candidate_actions_with_policy_aliases)
from llm_orchestrator.prompt_builder import build_inference_prompt, build_llm_input_object
from llm_orchestrator.validator import fallback_decision

BASE = "Qwen/Qwen2.5-1.5B-Instruct"
PRIORITY_BY_SFC = {"ReliableRelaySFC": "critical", "LowLatencyVideoSFC": "high",
                   "BandwidthOptimizedSFC": "medium", "EnergyAwareSFC": "low"}

# Mission name pools per intended SFC + telemetry samplers that hit each fallback
# branch. We label with fallback_decision and BIN by the returned SFC, so labels are
# always correct regardless of branch-order subtleties.
_MISSIONS = {
    "ReliableRelaySFC": ["emergency_alert_relay", "disaster_response_relay",
                         "critical_command_relay", "search_and_rescue_beacon"],
    "EnergyAwareSFC": ["long_term_soil_monitoring", "extended_perimeter_survey",
                       "low_power_crop_patrol"],
    "LowLatencyVideoSFC": ["real_time_pest_detection", "live_teleoperation_feed",
                           "realtime_obstacle_avoidance"],
    "BandwidthOptimizedSFC": ["bulk_data_transfer", "orthomosaic_map_upload",
                              "multispectral_dataset_sync"],
}
_GENERIC = ["routine_field_patrol", "scheduled_waypoint_run", "area_coverage_sweep"]


def _telemetry_for(target_sfc, rng):
    """Sample telemetry that lands on `target_sfc` under fallback_decision's rules."""
    if target_sfc == "ReliableRelaySFC":      # emergency mission OR loss>=3
        return dict(bw=rng.randint(10, 60), delay=rng.randint(15, 60),
                    loss=rng.choice([3, 4, 5, 6]), battery=rng.randint(50, 100))
    if target_sfc == "EnergyAwareSFC":        # soil mission OR battery<40
        return dict(bw=rng.randint(10, 40), delay=rng.randint(20, 60),
                    loss=rng.randint(0, 2), battery=rng.randint(10, 39))
    if target_sfc == "LowLatencyVideoSFC":    # pest mission OR delay<=10
        return dict(bw=rng.randint(20, 60), delay=rng.choice([4, 6, 8, 10]),
                    loss=rng.randint(0, 2), battery=rng.randint(50, 100))
    # BandwidthOptimized: must dodge the earlier branches -> loss<3, delay>10,
    # battery>=40, bw>=30
    return dict(bw=rng.randint(30, 100), delay=rng.randint(15, 60),
                loss=rng.randint(0, 2), battery=rng.randint(45, 100))


def _row(mission_type, t, sfc):
    return {"mission_type": mission_type, "priority": PRIORITY_BY_SFC[sfc],
            "configured_bandwidth_mbps": t["bw"], "configured_delay_ms": t["delay"],
            "configured_loss_percent": t["loss"], "battery_percent": t["battery"],
            "edge_compute_status": "available"}


def build_dataset(topo, cands, per_class, rng):
    """Balanced (prompt, target_json) pairs, labels from the oracle."""
    bins = {sfc: [] for sfc in PRIORITY_BY_SFC}
    tries = 0
    while min(len(v) for v in bins.values()) < per_class and tries < per_class * 200:
        tries += 1
        sfc = rng.choice(list(PRIORITY_BY_SFC))
        t = _telemetry_for(sfc, rng)
        # half the time use a named mission, half a generic one (forces telemetry use)
        mission = rng.choice(_MISSIONS[sfc]) if rng.random() < 0.5 else rng.choice(_GENERIC)
        io = build_llm_input_object(_row(mission, t, sfc), topo, cands, {})
        decision = fallback_decision(io)
        label = decision["selected_sfc"]
        if len(bins[label]) < per_class:
            prompt = build_inference_prompt(io)
            target = json.dumps({k: decision[k] for k in
                                 ("selected_sfc", "selected_policy", "selected_path",
                                  "selected_relay", "priority_class", "deployment_mode")})
            bins[label].append((prompt, target))
    data = [pair for v in bins.values() for pair in v]
    rng.shuffle(data)
    print("dataset per-SFC:", {k: len(v) for k, v in bins.items()}, "total", len(data))
    return data


def tokenize(tok, prompt, target, max_len):
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    t_ids = tok(target, add_special_tokens=False)["input_ids"] + [tok.eos_token_id]
    ids = (p_ids + t_ids)[:max_len]
    labels = ([-100] * len(p_ids) + t_ids)[:max_len]   # loss on the decision only
    return ids, labels


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", required=True)
    ap.add_argument("--out", default="netprompt_qwen_kg_rag_orchestrator/final_adapter_retrained")
    ap.add_argument("--per-class", type=int, default=160)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=2600)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)

    # 1) fixed topology + candidates from the KG (same as inference)
    kg = Neo4jContextClient(args.neo4j_uri, args.neo4j_user, args.neo4j_password)
    topo = build_compact_llm_topology_context(kg.get_topology_snapshot())
    cands = expand_candidate_actions_with_policy_aliases(kg.get_candidate_sfc_policy_set())
    kg.close()

    data = build_dataset(topo, cands, args.per_class, rng)

    # 2) model + fresh LoRA (match the original hyperparams)
    tok = AutoTokenizer.from_pretrained(BASE)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    # fp32 for training stability on the P100 (sm_60: no bf16; fp16 Adam underflows).
    # The saved LoRA weights are dtype-agnostic — inference still runs fp16.
    model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float32).to(args.device)
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    lora = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
                      task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                      "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()
    model.train()

    examples = [tokenize(tok, p, t, args.max_len) for p, t in data]
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr)

    # 3) train (batch 1 + grad accumulation; fp32 master via autocast off — LoRA in fp32)
    step = 0
    for epoch in range(args.epochs):
        rng.shuffle(examples)
        running = 0.0
        opt.zero_grad()
        for i, (ids, labels) in enumerate(examples):
            input_ids = torch.tensor([ids], device=args.device)
            label_ids = torch.tensor([labels], device=args.device)
            out = model(input_ids=input_ids, labels=label_ids)
            (out.loss / args.accum).backward()
            running += out.loss.item()
            if (i + 1) % args.accum == 0:
                torch.nn.utils.clip_grad_norm_(
                    [p for p in model.parameters() if p.requires_grad], 1.0)
                opt.step(); opt.zero_grad(); step += 1
            if (i + 1) % 80 == 0:
                print(f"epoch {epoch} ex {i+1}/{len(examples)} "
                      f"avg_loss {running/(i+1):.4f}", flush=True)
        print(f"=== epoch {epoch} done, mean loss {running/len(examples):.4f} ===", flush=True)

    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"saved retrained adapter -> {args.out}")


if __name__ == "__main__":
    main()
