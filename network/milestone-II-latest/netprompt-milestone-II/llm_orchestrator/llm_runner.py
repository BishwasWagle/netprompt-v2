from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import torch

from .prompt_builder import build_inference_prompt


DEBUG_DIR = Path("outputs")
DEBUG_DIR.mkdir(exist_ok=True)


class LLMOrchestrator:
    """Loads the base LLM and optional LoRA adapter for NetPrompt orchestration."""

    def __init__(
        self,
        model_name: str,
        adapter_path: Optional[str] = None,
        use_4bit: bool = True,
        device_map: str = "auto",
        max_new_tokens: int = 512,
    ):
        self.model_name = model_name
        self.adapter_path = adapter_path
        self.use_4bit = use_4bit
        self.device_map = device_map
        self.max_new_tokens = max_new_tokens
        self.tokenizer = None
        self.model = None

    def _cuda_available(self) -> bool:
        try:
            return torch.cuda.is_available()
        except Exception:
            return False

    def _resolved_device_map(self) -> Any:
        if not self._cuda_available():
            return {"": "cpu"}

        if self.device_map in ["cuda:0", "0", "gpu0"]:
            return {"": 0}

        if self.device_map == "auto":
            return "auto"

        if self.device_map == "cpu":
            return {"": "cpu"}

        return self.device_map

    def _resolved_dtype(self):
        if not self._cuda_available():
            return torch.float32
        return torch.float16

    def load(self) -> "LLMOrchestrator":
        from transformers import AutoModelForCausalLM, AutoTokenizer

        cuda_available = self._cuda_available()

        if not cuda_available:
            print("[INFO] CUDA/NVIDIA driver not available. Loading model on CPU.")
            print("[INFO] Disabling 4-bit quantization because bitsandbytes usually requires CUDA.")
            self.use_4bit = False

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            trust_remote_code=True,
        )

        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.tokenizer.padding_side = "left"

        quantization_config = None

        if self.use_4bit and cuda_available:
            from transformers import BitsAndBytesConfig

            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )

        model_kwargs = {
            "trust_remote_code": True,
            "device_map": self._resolved_device_map(),
        }

        if quantization_config is not None:
            model_kwargs["quantization_config"] = quantization_config
        else:
            model_kwargs["torch_dtype"] = self._resolved_dtype()

        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            **model_kwargs,
        )

        if self.adapter_path and Path(self.adapter_path).exists():
            from peft import PeftModel

            print(f"[INFO] Loading LoRA adapter from: {self.adapter_path}")
            self.model = PeftModel.from_pretrained(
                self.model,
                self.adapter_path,
            )
        elif self.adapter_path:
            print(f"[WARN] Adapter path does not exist; using base model only: {self.adapter_path}")

        self.model.eval()

        try:
            self.model.generation_config.do_sample = False
            self.model.generation_config.temperature = None
            self.model.generation_config.top_p = None
            self.model.generation_config.top_k = None
        except Exception:
            pass

        return self

    def _model_device(self):
        try:
            return self.model.device
        except Exception:
            return next(self.model.parameters()).device

    def generate_raw(
        self,
        input_object: Dict[str, Any],
        max_new_tokens: Optional[int] = None,
    ) -> str:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("LLMOrchestrator.load() must be called before generation.")

        prompt = build_inference_prompt(input_object)

        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "debug_llm_prompt.txt").write_text(prompt, encoding="utf-8")

        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=4096,
        )

        device = self._model_device()
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens or self.max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        generated_tokens = outputs[0][inputs["input_ids"].shape[-1]:]

        raw = self.tokenizer.decode(
            generated_tokens,
            skip_special_tokens=False,
        )

        (DEBUG_DIR / "debug_raw_model_output.txt").write_text(raw, encoding="utf-8")

        return raw

    def generate_decision(self, input_object: Dict[str, Any]) -> Dict[str, Any]:
        raw = self.generate_raw(input_object)
        return parse_model_decision(raw)


def extract_assistant_section(text: str) -> str:
    """
    The model may output valid JSON followed by extra chat turns.
    Do not split away the JSON if it appears before assistant markers.
    """
    if "{" in text:
        return text[text.find("{"):].strip()

    markers = [
        "<|assistant|>",
        "<|im_start|>assistant",
        "assistant",
    ]

    for marker in markers:
        if marker in text:
            return text.split(marker)[-1].strip()

    return text.strip()

def extract_first_json_object(text: str) -> Dict[str, Any]:
    start = text.find("{")

    if start == -1:
        raise ValueError("No JSON object start found in model output.")

    brace_count = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            brace_count += 1

        elif ch == "}":
            brace_count -= 1

            if brace_count == 0:
                candidate = text[start:i + 1]

                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Try a stricter fallback: remove anything after the first valid closing brace.
                    cleaned = candidate.strip()
                    return json.loads(cleaned)

    raise ValueError("No complete JSON object found in model output.")

def safe_default_decision() -> Dict[str, Any]:
    """
    Safe fallback used only when CPU inference produces no parseable JSON.
    This keeps the runtime alive but should be reported separately from
    successful LLM-generated JSON in experiments.
    """
    return {
        "selected_sfc": "ReliableRelaySFC",
        "selected_policy": "backup_path_reliable_relay",
        "selected_path": "backup",
        "selected_relay": "s3",
        "priority_class": "critical",
        "deployment_mode": "multihop",
        "llm_parse_status": "failed_no_json_used_safe_default",
    }


def parse_model_decision(raw_output: str) -> Dict[str, Any]:
    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / "debug_raw_model_output_parse_stage.txt").write_text(
        raw_output,
        encoding="utf-8",
    )

    assistant_text = extract_assistant_section(raw_output)

    try:
        decision = extract_first_json_object(assistant_text)
        decision["llm_parse_status"] = "parsed_json"
        return decision
    except Exception as exc:
        print(f"[WARN] Could not parse JSON from model output: {exc}")
        print("[WARN] Raw model output saved to outputs/debug_raw_model_output.txt")
        print("[WARN] Using safe default decision so the pipeline can continue.")
        return safe_default_decision()
