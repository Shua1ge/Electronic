"""Smoke-test four prepared PDF previews; this does not score factual accuracy."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "outputs/verification/pdf_samples"
inventory = json.loads((out / "inventory.json").read_text())
prompt = ('从图右下角标题栏提取图名、工程名称、设计单位。只返回JSON对象，'
          '键为drawing_title、project_name、design_institute，值只能是字符串或null。'
          '只读图中可见文字，不能看清时为null，不要根据常识补全。不输出其他内容。')
summary = []
for index, item in enumerate(inventory, 1):
    image = out / f"vision-{index}.png"
    target = out / f"vision-{index}.json"
    print(f"Vision sample {index}/{len(inventory)}: {Path(item['file']).name}", flush=True)
    completed = subprocess.run([str(ROOT / "scripts/offline-python"), str(ROOT / "scripts/infer_image.py"),
                                str(image), "--output", str(target), "--prompt", prompt,
                                "--max-tokens", "256"], cwd=ROOT, capture_output=True, text=True, timeout=180)
    (out / f"vision-{index}.log").write_text(completed.stdout + completed.stderr)
    completed.check_returncode()
    result = json.loads(target.read_text())
    parsed = result["parsed"]
    valid = (isinstance(parsed, dict) and set(parsed) == {"drawing_title", "project_name", "design_institute"}
             and all(v is None or isinstance(v, str) for v in parsed.values()))
    summary.append({"source_pdf": item["file"], "page": 3 if index < 4 else 2,
                    "json_format_valid": valid, "factual_accuracy_scored": False,
                    "extracted": parsed, "generation_seconds": result["generation_seconds"],
                    "mlx_peak_memory_gb": result["mlx_peak_memory_gb"]})
    print(json.dumps(summary[-1], ensure_ascii=False), flush=True)
(out / "vision-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
assert all(s["json_format_valid"] for s in summary), "Some responses failed JSON validation; see raw outputs"
