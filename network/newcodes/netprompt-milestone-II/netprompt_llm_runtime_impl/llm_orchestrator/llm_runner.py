from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import torch

from .prompt_builder import build_inference_prompt


class LLMOrchestrator:
    """Loads the base LLM and optional LoRA adapter for NetPrompt orchestration."""

    def __init__(
        self,
        model_name: str,
        adapter_path: Optional[str] = None,
        use_4bit: bool = True,
        device_map: str = "cuda:0",
        max_new_tokens: int = 128,
    ):
        self.model_name = model_name
        self.adapter_path = adapter_path
        self.use_4bit = use_4bit
        self.device_map = device_map
        self.max_new_tokens = max_new_tokens
        self.tokenizer = None
        self.model = None

    def _hf_device_map(self) -> Any:
        if self.device_map in ["cuda:0", "0", "gpu0"]:
            return {"": 0}
        if self.device_map == "auto":
            return "auto"
        if self.device_map == "cpu":
            return {"": "cpu"}
        return self.device_map

    def load(self) -> "LLMOrchestrator":
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "right"

        quantization_config = None
        if self.use_4bit:
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )

        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            quantization_config=quantization_config,
            device_map=self._hf_device_map(),
            trust_remote_code=True,
        )

        if self.adapter_path and Path(self.adapter_path).exists():
            from peft import PeftModel

            self.model = PeftModel.from_pretrained(self.model, self.adapter_path)
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

    def generate_raw(self, input_object: Dict[str, Any], max_new_tokens: Optional[int] = None) -> str:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("LLMOrchestrator.load() must be called before generation.")

        prompt = build_inference_prompt(input_object)
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=4096,
        ).to(self.model.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens or self.max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        generated_tokens = outputs[0][inputs["input_ids"].shape[-1] :]
        return self.tokenizer.decode(generated_tokens, skip_special_tokens=False)

    def generate_decision(self, input_object: Dict[str, Any]) -> Dict[str, Any]:
        raw = self.generate_raw(input_object)
        return parse_model_decision(raw)


def extract_assistant_section(text: str) -> str:
    marker = "<|assistant|>"
    if marker in text:
        return text.split(marker)[-1].strip()
    return text.strip()


def extract_first_json_object(text: str) -> Dict[str, Any]:
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object start found in model output.")

    brace_count = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            brace_count += 1
        elif text[i] == "}":
            brace_count -= 1
            if brace_count == 0:
                candidate = text[start : i + 1]
                return json.loads(candidate)
    raise ValueError("No complete JSON object found in model output.")


def parse_model_decision(raw_output: str) -> Dict[str, Any]:
    assistant_text = extract_assistant_section(raw_output)
    return extract_first_json_object(assistant_text)
