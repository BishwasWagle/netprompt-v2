from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RuntimeConfig:
    """Runtime settings for NetPrompt LLM orchestration."""

    netprompt_root: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: Optional[str]
    model_name: str
    adapter_path: Optional[str]
    results_csv: Optional[str]
    output_dir: str
    device_map: str
    max_new_tokens: int
    use_4bit: bool

    @classmethod
    def from_env(
        cls,
        netprompt_root: Optional[str] = None,
        neo4j_uri: Optional[str] = None,
        neo4j_user: Optional[str] = None,
        neo4j_password: Optional[str] = None,
        model_name: Optional[str] = None,
        adapter_path: Optional[str] = None,
        results_csv: Optional[str] = None,
        output_dir: Optional[str] = None,
        device_map: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
        use_4bit: Optional[bool] = None,
    ) -> "RuntimeConfig":
        root = netprompt_root or os.getenv("NETPROMPT_ROOT") or str(Path.cwd())
        return cls(
            netprompt_root=root,
            neo4j_uri=neo4j_uri or os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=neo4j_user or os.getenv("NEO4J_USER", "neo4j"),
            neo4j_password=neo4j_password or os.getenv("NEO4J_PASSWORD"),
            model_name=model_name or os.getenv("NETPROMPT_LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct"),
            adapter_path=adapter_path or os.getenv(
                "NETPROMPT_LLM_ADAPTER",
                str(Path(root) / "netprompt_qwen_kg_rag_orchestrator" / "final_adapter"),
            ),
            results_csv=results_csv or os.getenv(
                "NETPROMPT_RESULTS_CSV",
                str(Path(root) / "results" / "final_milestone2_results_clean.csv"),
            ),
            output_dir=output_dir or os.getenv("NETPROMPT_LLM_OUTPUT_DIR", str(Path(root) / "outputs")),
            device_map=device_map or os.getenv("NETPROMPT_LLM_DEVICE_MAP", "cuda:0"),
            max_new_tokens=int(max_new_tokens or os.getenv("NETPROMPT_LLM_MAX_NEW_TOKENS", "128")),
            use_4bit=use_4bit if use_4bit is not None else os.getenv("NETPROMPT_LLM_USE_4BIT", "1") != "0",
        )
