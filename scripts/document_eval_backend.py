"""Local Qwen3.5 vision backends; imported only for real inference."""
from __future__ import annotations

import base64
import gc
import importlib.metadata
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from document_eval_core import digest, require


def software_versions():
    result = {}
    for name in ("torch", "torchvision", "transformers", "accelerate", "bitsandbytes", "huggingface-hub",
                 "PyMuPDF", "Pillow", "numpy", "scipy"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def compute_dtype_name(capability):
    # Turing (2080 Ti, SM 7.5) has no native BF16. Avoid emulation-based detection.
    return "bfloat16" if capability[0] >= 8 else "float16"


class TransformersQwen:
    def __init__(self, args):
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig
        require(torch.cuda.is_available(), "CUDA 不可用；请检查 PyTorch CUDA wheel 和显卡驱动")
        self.torch, self.args = torch, args
        torch.cuda.set_device(0)
        capability = torch.cuda.get_device_capability(0)
        dtype_name = compute_dtype_name(capability)
        dtype = getattr(torch, dtype_name)
        # Resolve once so processor and weights always use the same local snapshot.
        location = Path(args.model_id).expanduser()
        if not location.is_dir():
            from huggingface_hub import snapshot_download
            location = Path(snapshot_download(args.model_id, revision=args.revision,
                                             local_files_only=not args.online))
        location = location.resolve()
        config = json.loads((location / "config.json").read_text(encoding="utf-8"))
        require(not config.get("quantization_config"),
                "Transformers backend expects original weights, then applies NF4. For GGUF/AWQ/prequantized models use --backend openai with your local server.")
        self.processor = AutoProcessor.from_pretrained(str(location), local_files_only=True)
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                  bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
        started = time.perf_counter()
        self.model = AutoModelForMultimodalLM.from_pretrained(
            str(location), quantization_config=quant, dtype=dtype, device_map={"": 0},
            low_cpu_mem_usage=True, attn_implementation="sdpa", local_files_only=True,
        ).eval()
        torch.cuda.synchronize()
        props = torch.cuda.get_device_properties(0)
        self.metadata = {
            "backend": "transformers", "model_id": args.model_id,
            "requested_revision": args.revision,
            "resolved_revision": location.name if location.parent.name == "snapshots" else config.get("_commit_hash"),
            "checkpoint_config_sha256": digest(location / "config.json"),
            "checkpoint_model_type": config.get("model_type"), "checkpoint_architectures": config.get("architectures"),
            "quantization_config": quant.to_dict(),
            "local_weight_files": [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(location.glob("*.safetensors"))],
            "weight_content_sha256": None,
            "quantization": "bitsandbytes NF4 + double quant", "compute_dtype": dtype_name,
            "attention": "sdpa", "enable_thinking": False, "do_sample": False,
            "gpu": props.name, "compute_capability": list(capability),
            "gpu_total_gib": props.total_memory/1024**3, "cuda_runtime": torch.version.cuda,
            "load_seconds": time.perf_counter()-started,
            "note": "Local checkpoint weight content is not hashed; archive the checkpoint separately for reproducibility.",
        }

    def infer(self, prompt, images, seed):
        torch = self.torch
        content = []
        for item in images:
            content += [{"type": "text", "text": f"image_id={item['id']}"},
                        {"type": "image", "path": str(item["path"])}]
        content.append({"type": "text", "text": prompt})
        started = time.perf_counter()
        torch.cuda.reset_peak_memory_stats()
        inputs = self.processor.apply_chat_template(
            [{"role": "user", "content": content}], tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors="pt", enable_thinking=False,
            # Prevent processor defaults from upscaling many context images.
            images_kwargs={"size": {"shortest_edge": 32*32, "longest_edge": self.args.max_pixels//len(images)}},
        ).to("cuda:0")
        input_tokens = inputs["input_ids"].shape[-1]
        require(input_tokens <= self.args.max_input_tokens,
                f"input token budget exceeded: {input_tokens}>{self.args.max_input_tokens}; reduce --max-pixels or context images")
        torch.manual_seed(seed)
        torch.cuda.synchronize()
        generation_started = time.perf_counter()
        with torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=self.args.max_new_tokens,
                                         do_sample=False, use_cache=True)
        torch.cuda.synchronize()
        generation_seconds = time.perf_counter() - generation_started
        tokens = output[0][input_tokens:]
        raw = self.processor.decode(tokens, skip_special_tokens=True)
        grid = inputs.get("image_grid_thw")
        result = {"raw": raw, "input_tokens": int(input_tokens), "generated_tokens": len(tokens),
                  "max_tokens_reached": len(tokens) >= self.args.max_new_tokens,
                  "seconds": time.perf_counter()-started, "generation_seconds": generation_seconds,
                  "peak_allocated_gib": torch.cuda.max_memory_allocated()/1024**3,
                  "peak_reserved_gib": torch.cuda.max_memory_reserved()/1024**3,
                  "image_grid_thw": grid.tolist() if grid is not None else None}
        del output, inputs, tokens
        return result

    def recover(self):
        gc.collect()
        self.torch.cuda.empty_cache()


class OpenAICompatibleQwen:
    """For an already running local Ollama/llama.cpp/vLLM vision server."""
    def __init__(self, args):
        self.args = args
        require(bool(args.api_url), "--backend openai requires --api-url, e.g. http://127.0.0.1:8000/v1")
        self.metadata = {"backend": "openai", "model_id": args.model_id, "api_url": args.api_url,
                         "resolved_revision": args.revision, "server_quantization": args.server_quantization,
                         "compute_dtype": None, "do_sample": False,
                         "note": "Client requests temperature=0. Server implementation, revision and quantization must be independently recorded; determinism is not guaranteed."}

    def infer(self, prompt, images, seed):
        content = []
        for item in images:
            encoded = base64.b64encode(Path(item["path"]).read_bytes()).decode("ascii")
            content += [{"type": "text", "text": f"image_id={item['id']}"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}}]
        content.append({"type": "text", "text": prompt})
        body = {"model": self.args.model_id, "messages": [{"role": "user", "content": content}],
                "max_tokens": self.args.max_new_tokens, "temperature": 0, "seed": seed, "stream": False}
        # No silent retry on servers rejecting optional fields: exact request is recorded.
        body.update(self.args.api_extra)
        request = urllib.request.Request(self.args.api_url.rstrip("/") + "/chat/completions",
                                         data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.args.api_timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        choices = data.get("choices", [])
        require(len(choices) == 1 and isinstance(choices[0].get("message", {}).get("content"), str), "invalid chat completions response")
        usage = data.get("usage") or {}
        return {"raw": choices[0]["message"]["content"], "seconds": time.perf_counter()-started,
                "input_tokens": usage.get("prompt_tokens"), "generated_tokens": usage.get("completion_tokens"),
                "max_tokens_reached": choices[0].get("finish_reason") == "length",
                "finish_reason": choices[0].get("finish_reason"), "server_model": data.get("model"),
                "peak_allocated_gib": None, "peak_reserved_gib": None,
                "input_token_limit_enforced": False}

    def recover(self):
        pass
