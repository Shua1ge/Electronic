"""Run a local image through the downloaded model. JSON validity is not factual accuracy."""
import argparse
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ.setdefault("HF_HOME", str(ROOT / ".cache/huggingface"))

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("image", type=Path)
parser.add_argument("--output", type=Path, default=ROOT / "outputs/vision.json")
parser.add_argument("--prompt", default='读取图中两站之间本工程新建光缆的标注。只返回一个JSON对象，字段为 cable_type（只填类型英文缩写，如OPGW或ADSS，不加中文描述，不清楚填null）、cores（整数或null）、length_km（数字或null）、visible_station_names（这条光缆两端站名的字符串数组）。只填写图片清晰可见的信息，不能推测。')
parser.add_argument("--max-tokens", type=int, default=512)
args = parser.parse_args()
if not args.image.is_file():
    parser.error("Image must be an existing local file")

import mlx.core as mx
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config

model_path = ROOT / "models/Qwen3.5-4B-MLX-4bit"
start = time.perf_counter()
model, processor = load(str(model_path))
load_seconds = time.perf_counter() - start
prompt = apply_chat_template(processor, load_config(str(model_path)), args.prompt,
                             num_images=1, enable_thinking=False)
start = time.perf_counter()
result = generate(model, processor, prompt, [str(args.image.resolve())],
                  max_tokens=args.max_tokens, temperature=0, verbose=False)
elapsed = time.perf_counter() - start
raw = result.text if hasattr(result, "text") else str(result)
cleaned = raw.strip()
if cleaned.startswith("```json") and cleaned.endswith("```"):
    cleaned = cleaned[7:-3].strip()
elif cleaned.startswith("```") and cleaned.endswith("```"):
    cleaned = cleaned[3:-3].strip()
try:
    parsed = json.loads(cleaned)
    parse_error = None
except json.JSONDecodeError as error:
    parsed, parse_error = None, str(error)
report = {"source": str(args.image.resolve()), "model": str(model_path), "raw": raw,
          "parsed": parsed, "parse_error": parse_error, "load_seconds": load_seconds,
          "generation_seconds": elapsed, "mlx_peak_memory_gb": mx.get_peak_memory() / 1e9,
          "hf_offline": True, "metal_available": mx.metal.is_available()}
for name in ("prompt_tokens", "generation_tokens", "prompt_tps", "generation_tps"):
    if hasattr(result, name):
        report[name] = getattr(result, name)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
