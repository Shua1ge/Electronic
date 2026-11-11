#!/usr/bin/env python3
"""Frozen-model, within-document perception evaluation. See docs/document-eval.md."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import traceback
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from document_eval_core import (VERSION, digest, evidence_counts, json_hash, parse_prediction,
                                prf, ratio, read_json, require, score_case, validate_gold,
                                validate_manifest, write_json)

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = Path(__file__).with_name("document_eval_prompt.txt")


def new_directory(path):
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def initialize(args):
    import pymupdf
    source = args.pdf.expanduser().resolve()
    require(source.is_file(), f"PDF not found: {source}")
    pages = list(dict.fromkeys(args.pages))
    with pymupdf.open(source) as doc:
        require(all(1 <= p <= len(doc) for p in pages), "page number outside PDF")
    manifest = {"schema_version": "1.0", "study_role": "development",
                "documents": [{"id": args.document_id, "path": str(source), "sha256": digest(source),
                               "project_group_id": None, "source_group_id": None}],
                "cases": [{"id": f"{args.document_id}-p{p:03d}-{args.region_id}-A", "document_id": args.document_id,
                           "page": p, "region_id": args.region_id, "target_bbox": args.bbox,
                           "condition": "A", "contexts": [], "items": []} for p in pages]}
    validate_manifest(manifest)
    out = new_directory(args.output)
    write_json(out / "manifest.json", manifest)
    write_json(out / "gold_template.json", {"schema_version": "1.0", "manifest_sha256": json_hash(manifest),
               "annotation_status": "pending", "reviewer": None, "cases": {c["id"]: None for c in manifest["cases"]}})
    print(f"Created {out / 'manifest.json'}; gold is pending, not an empty truth set.")


def render_image(page, bbox, destination, pixel_budget, long_edge, rotation_degrees=0):
    """Render the default rotated page; bbox is in that displayed coordinate frame."""
    import pymupdf
    from PIL import Image
    w, h = page.rect.width, page.rect.height
    clip = pymupdf.Rect(bbox[0]*w, bbox[1]*h, bbox[2]*w, bbox[3]*h)
    scale = min(long_edge/max(clip.width, clip.height), math.sqrt(pixel_budget/(clip.width*clip.height)))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False, colorspace=pymupdf.csRGB)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # Pixel rounding can exceed budget by a few rows. Only shrink, never enlarge.
    if image.width*image.height > pixel_budget:
        factor = math.sqrt(pixel_budget/(image.width*image.height))
        image = image.resize((max(1, int(image.width*factor)), max(1, int(image.height*factor))), Image.Resampling.LANCZOS)
    effective_bbox = [max(0., pix.x/scale/w), max(0., pix.y/scale/h),
                      min(1., (pix.x+pix.width)/scale/w), min(1., (pix.y+pix.height)/scale/h)]
    require(rotation_degrees in {0, 90, 180, 270}, "rotation must be a multiple of 90")
    if rotation_degrees:
        transpose = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
        image = image.transpose(transpose[rotation_degrees])
    image.save(destination)
    x0, y0, x1, y1 = effective_bbox
    dx, dy = x1-x0, y1-y0
    affine = {0: [dx, 0, x0, 0, dy, y0], 90: [0, dx, x0, -dy, 0, y1],
              180: [-dx, 0, x1, 0, -dy, y1], 270: [0, -dx, x1, dy, 0, y0]}[rotation_degrees]
    return {"path": str(destination.resolve()), "sha256": digest(destination),
            "width": image.width, "height": image.height, "pixels": image.width*image.height,
            "page_bbox": effective_bbox, "requested_bbox": bbox, "pdf_rotation": page.rotation,
            "extra_rotation_clockwise": rotation_degrees, "image_to_page_affine": affine,
            "displayed_page_size_pt": [w, h]}


def prepare_case(case, source, out, args):
    import pymupdf
    specs = [{"id": "target", "page": case["page"], "bbox": case["target_bbox"], "rotation_degrees": case.get("rotation_degrees", 0), "purpose": "target region"}]
    specs += [{"id": f"context{i}", **{k: context[k] for k in ("page", "bbox", "purpose")}}
               | {"rotation_degrees": context.get("rotation_degrees", 0)} for i, context in enumerate(case.get("contexts", []), 1)]
    image_dir = out / "images" / case["id"]
    image_dir.mkdir(parents=True)
    # Equal per-image budget, recorded explicitly; A/B/C total budget stays fixed.
    per_image = args.max_pixels // len(specs)
    images = []
    with pymupdf.open(source) as doc:
        for spec in specs:
            require(1 <= spec["page"] <= len(doc), f"{case['id']}: page outside PDF")
            meta = render_image(doc[spec["page"]-1], spec["bbox"], image_dir / f"{spec['id']}.png", per_image, args.long_edge, spec.get("rotation_degrees", 0))
            images.append({**meta, "id": spec["id"], "page": spec["page"], "purpose": spec["purpose"]})
    inputs = [{k: v for k, v in img.items() if k != "path"} for img in images]
    from document_eval_prompts import task_prompt
    prompt = task_prompt(case.get("task_type", "diagram")) + "\n本次任务：\n" + json.dumps({
        "document_id": case["document_id"], "page": case["page"], "region_id": case["region_id"],
        "condition": case["condition"], "target_bbox": images[0]["page_bbox"],
        "images": inputs, "predeclared_items": case.get("items", [])}, ensure_ascii=False, indent=2)
    signature = json_hash({"case": case, "images": inputs, "prompt": prompt})
    return images, prompt, signature


def code_provenance():
    files = [Path(__file__), Path(__file__).with_name("document_eval_core.py"),
             Path(__file__).with_name("document_eval_backend.py"), PROMPT_PATH,
             Path(__file__).with_name("document_eval_tasks.py"), Path(__file__).with_name("document_eval_prompts.py"),
             Path(__file__).with_name("document_annotation.py")]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {"git_commit": commit, "files_sha256": {p.name: digest(p) for p in files}}


def prediction_to_page(prediction, case, images):
    """Keep raw model boxes in crop coordinates, deterministically map for scoring."""
    if case.get("task_type", "diagram") != "diagram":
        return prediction
    import copy
    mapped = copy.deepcopy(prediction)
    a, b, c, d, e, f = images[0]["image_to_page_affine"]
    for obj in mapped["objects"]:
        x0, y0, x1, y1 = obj["bbox"]
        corners = [(a*x+b*y+c, d*x+e*y+f) for x in (x0, x1) for y in (y0, y1)]
        obj["bbox"] = [max(0., min(p[0] for p in corners)), max(0., min(p[1] for p in corners)),
                       min(1., max(p[0] for p in corners)), min(1., max(p[1] for p in corners))]
    return mapped


def execute(args, backend_factory=None):
    from document_eval_backend import software_versions
    run_started = time.perf_counter()
    manifest_file = args.manifest.expanduser().resolve()
    manifest = validate_manifest(read_json(manifest_file))
    sources = {}
    for doc in manifest["documents"]:
        path = Path(doc["path"]).expanduser()
        if not path.is_absolute():
            path = manifest_file.parent / path
        require(path.is_file() and digest(path) == doc["sha256"], f"PDF missing/changed: {doc['id']}")
        sources[doc["id"]] = path.resolve()
    out = new_directory(args.output)
    for folder in ("calls", "predictions"):
        (out / folder).mkdir()
    write_json(out / "manifest.json", manifest)
    config = {k: v for k, v in vars(args).items() if k not in {"func", "manifest", "output", "responses_dir"}}
    config = {k: str(v) if isinstance(v, Path) else v for k, v in config.items()}
    run = {"version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
           "manifest_sha256": json_hash(manifest), "config": config, "config_sha256": json_hash(config),
           "code": code_provenance(), "software": software_versions(), "python": sys.version,
           "platform": platform.platform(), "model": None, "backend_load_error": None,
           "replay_origin": str(args.responses_dir.resolve()) if args.responses_dir else None,
           "cases": [], "completed": False}
    write_json(out / "run.json", run)
    records, backend, load_failed = [], None, False
    try:
        if args.backend != "replay":
            if backend_factory is not None:
                backend = backend_factory(args)
            elif args.backend == "transformers":
                from document_eval_backend import TransformersQwen
                backend = TransformersQwen(args)
            else:
                from document_eval_backend import OpenAICompatibleQwen
                backend = OpenAICompatibleQwen(args)
            run["model"] = backend.metadata
        else:
            require(args.responses_dir and (args.responses_dir / "run.json").is_file(), "replay requires the original run directory with run.json/calls/")
            origin = read_json(args.responses_dir / "run.json")
            run["model"] = {"backend": "replay", "original_model": origin.get("model"),
                            "note": "No new model inference; runtime costs are unavailable."}
    except Exception as exc:
        load_failed = True
        run["backend_load_error"] = {"type": type(exc).__name__, "message": str(exc)}
        print(f"Backend failed: {exc}", file=sys.stderr)
    write_json(out / "run.json", run)
    for index, case in enumerate(manifest["cases"]):
        record = {"case_id": case["id"], "document_id": case["document_id"], "page": case["page"],
                  "region_id": case["region_id"], "condition": case["condition"],
                  "task_type": case.get("task_type", "diagram"),
                  "status": "execution_error", "error": None, "prediction": None,
                  "input_signature": None, "images": [], "raw": None, "usage": {}, "replayed": args.backend == "replay"}
        try:
            images, prompt, signature = prepare_case(case, sources[case["document_id"]], out, args)
            record.update(input_signature=signature, images=[{**i, "path": str(Path(i["path"]).relative_to(out))} for i in images], prompt=prompt)
            if load_failed:
                raise RuntimeError(f"backend_load_error: {run['backend_load_error']['message']}")
            if args.backend == "replay":
                original = read_json(args.responses_dir / "calls" / f"{case['id']}.json")
                require(original.get("input_signature") == signature, "replay input signature mismatch; restore original manifest/pixel budget/prompt/code")
                require(isinstance(original.get("raw"), str), "original call has no raw response")
                result = {"raw": original["raw"], "max_tokens_reached": original["usage"].get("max_tokens_reached")}
            else:
                result = backend.infer(prompt, images, args.seed + index)
            record["raw"] = result["raw"]
            record["usage"] = {k: v for k, v in result.items() if k != "raw"}
            try:
                record["prediction"] = prediction_to_page(parse_prediction(result["raw"], case), case, images)
                record["status"] = "ok"
                record["evidence"] = evidence_counts(record["prediction"], {i["id"] for i in images})
                write_json(out / "predictions" / f"{case['id']}.json", record["prediction"])
            except (ValueError, TypeError, KeyError) as exc:
                record["status"] = "invalid_output"
                record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        except Exception as exc:
            record["status"] = "execution_error"
            record["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
            if backend:
                backend.recover()
        write_json(out / "calls" / f"{case['id']}.json", record)
        records.append(record)
        run["cases"].append({"id": case["id"], "status": record["status"], "call_sha256": digest(out / "calls" / f"{case['id']}.json")})
        write_json(out / "run.json", run)
        # Incremental metrics survive an interrupted or failed long GPU run.
        write_metrics(out, records, manifest, None, args.iou_threshold)
        print(f"[{index+1}/{len(manifest['cases'])}] {case['id']}: {record['status']}", flush=True)
    run["completed"] = True
    run["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    run["total_seconds_including_load_and_render"] = time.perf_counter()-run_started
    write_json(out / "run.json", run)
    print(f"Results: {out}; metrics: {out / 'metrics.json'}")
    return 2 if any(r["status"] == "execution_error" for r in records) else (3 if any(r["status"] != "ok" for r in records) else 0)


def aggregate_accuracy(results):
    if not results:
        return None
    if results[0].get("task_type") in {"text", "table"}:
        from document_eval_tasks import aggregate_tasks
        return aggregate_tasks(results)
    merged = {}
    for field in ("objects", "connections"):
        merged[field] = prf(*(sum(r[field][k] for r in results) for k in ("tp", "fp", "fn")))
    for field in ("object_kind_conditional", "object_parent_conditional", "object_name_conditional", "object_name_end_to_end", "region_role"):
        merged[field] = ratio(sum(r[field]["correct"] for r in results), sum(r[field]["total"] for r in results))
    relations = sorted({rel for r in results for rel in r["connections_by_relation"]})
    merged["connections_by_relation"] = {rel: prf(*(sum(r["connections_by_relation"].get(rel, {}).get(k, 0) for r in results)
                                                     for k in ("tp", "fp", "fn"))) for rel in relations}
    merged["attributes"] = {}
    for field in results[0]["attributes"]:
        vals = [r["attributes"][field] for r in results]
        merged["attributes"][field] = {scope: ratio(sum(v[scope]["correct"] for v in vals), sum(v[scope]["total"] for v in vals))
                                        for scope in ("conditional", "end_to_end")}
        merged["attributes"][field]["claims_on_unknown_gold"] = sum(v["claims_on_unknown_gold"] for v in vals)
    ints = ("eligible", "answered", "wrong", "known", "known_answered", "unknown", "unknown_explicit_abstentions", "omitted")
    sel = {k: sum(r["selective"][k] for r in results) for k in ints}
    sel.update(coverage=sel["answered"]/sel["eligible"] if sel["eligible"] else None,
               selective_error=sel["wrong"]/sel["answered"] if sel["answered"] else None,
               known_answer_coverage=sel["known_answered"]/sel["known"] if sel["known"] else None)
    merged["selective"] = sel
    merged["parallel_matches_without_attribute_score"] = sum(r["parallel_matches_without_attribute_score"] for r in results)
    return merged


def group_metrics(records, cases, gold, threshold):
    counts = Counter(r["status"] for r in records)
    valid = [r for r in records if r["status"] == "ok"]
    latency = [r["usage"]["seconds"] for r in records if isinstance(r["usage"].get("seconds"), (int, float))]
    evidence = sum(r.get("evidence", {}).get("references", 0) for r in valid)
    valid_evidence = sum(r.get("evidence", {}).get("valid_references", 0) for r in valid)
    annotated = [r for r in records if gold and gold["cases"].get(r["case_id"]) is not None]
    scored = {r["case_id"]: score_case(r["prediction"], gold["cases"][r["case_id"]], threshold) for r in annotated}
    runtime = {"cases_planned": len(cases), "cases_recorded": len(records), "ok": counts["ok"],
               "execution_errors": counts["execution_error"], "invalid_outputs": counts["invalid_output"],
               "valid_output_rate": counts["ok"]/len(cases) if cases else None,
               "truncated_responses": sum(r["usage"].get("max_tokens_reached") is True for r in records),
               "mean_call_seconds": statistics.mean(latency) if latency else None,
               "median_call_seconds": statistics.median(latency) if latency else None,
               "peak_reserved_gib": max((r["usage"].get("peak_reserved_gib") or 0 for r in records), default=0) or None,
               "total_input_pixels": sum(i["pixels"] for r in records for i in r["images"]),
               "input_tokens_reported": sum(r["usage"].get("input_tokens") or 0 for r in records),
               "generated_tokens_reported": sum(r["usage"].get("generated_tokens") or 0 for r in records),
               "token_usage_available_cases": sum(r["usage"].get("input_tokens") is not None for r in records),
               "replayed_cases": sum(r["replayed"] for r in records)}
    return {"runtime": runtime,
            "evidence_references": {"references": evidence, "valid_references": valid_evidence,
                                    "reference_validity": valid_evidence/evidence if evidence else None,
                                    "semantic_support_accuracy": None,
                                    "note": "Valid image id and bbox do not establish factual support. Expert review required."},
            "gold_cases_scored": len(scored), "gold_cases_unscored": len(cases)-len(scored),
            "accuracy": aggregate_accuracy(list(scored.values())), "case_scores": scored}


def flatten(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from flatten(child, f"{path}.{key}" if path else key)
    elif value is None or isinstance(value, (str, int, float, bool)):
        yield path, value


def write_metrics(out, records, manifest, gold, threshold):
    groups = defaultdict(list)
    for case in manifest["cases"]:
        groups[(case["document_id"], case["condition"], case.get("task_type", "diagram"))].append(case)
    metrics = {"version": VERSION, "manifest_sha256": json_hash(manifest), "iou_threshold": threshold,
               "gold_available": gold is not None, "gold_sha256": json_hash(gold) if gold else None,
               "note": "No gold => accuracy=null. Runtime/format/reference validity are not recognition accuracy. Execution/parse failures remain in end-to-end recall. Context-selected C is oracle diagnostic.",
               "groups": {}}
    for (doc, condition, task_type), cases in sorted(groups.items()):
        ids = {c["id"] for c in cases}
        metrics["groups"][f"{doc}/{condition}/{task_type}"] = group_metrics([r for r in records if r["case_id"] in ids], cases, gold, threshold)
    write_json(out / "metrics.json", metrics)
    with (out / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["group", "metric", "value"])
        for group, values in metrics["groups"].items():
            for key, value in flatten({k: v for k, v in values.items() if k != "case_scores"}):
                writer.writerow([group, key, "NA" if value is None else value])
    lines = [f"{VERSION} — 文档内识别评测", f"人工真值：{'已提供并校验' if gold else '未提供；所有准确率为 NA'}",
             f"对象 IoU 阈值：{threshold}（须在查看预测前固定）", ""]
    for key, group in metrics["groups"].items():
        runtime, acc = group["runtime"], group["accuracy"]
        lines += [key, f"计划 {runtime['cases_planned']}，有效输出 {runtime['ok']}，解析/结构错误 {runtime['invalid_outputs']}，执行错误 {runtime['execution_errors']}，截断 {runtime['truncated_responses']}",
                  f"有效输出率 {runtime['valid_output_rate']}；平均调用秒数 {runtime['mean_call_seconds']}；峰值 reserved GiB {runtime['peak_reserved_gib']}"]
        if acc and "objects" in acc:
            lines += [f"对象 P/R/F1：{acc['objects']['precision']} / {acc['objects']['recall']} / {acc['objects']['f1']}",
                      f"连接 P/R/F1：{acc['connections']['precision']} / {acc['connections']['recall']} / {acc['connections']['f1']}",
                      f"选择性回答覆盖率：{acc['selective']['coverage']}；已回答项错误率：{acc['selective']['selective_error']}"]
        elif acc:
            field = "transcription" if acc["task_type"] == "text" else "cell_text_end_to_end"
            lines += [f"文字 CER：{acc[field]['cer']}；精确匹配率：{acc[field]['exact_match']}",
                      f"判断项覆盖率：{acc['selective']['coverage']}；已回答项错误率：{acc['selective']['selective_error']}"]
            if "cell_structure" in acc:
                lines += [f"表格单元格结构 F1：{acc['cell_structure']['f1']}；行列/表头准确率：{acc['dimensions']['accuracy']}"]
        else:
            lines += ["识别准确率、P/R/F1、选择性错误率：NA"]
        lines += ["证据语义支持率：NA（需专家审查）", ""]
    (out / "metrics.txt").write_text("\n".join(lines), encoding="utf-8")
    return metrics


def score(args):
    source = args.run.expanduser().resolve()
    manifest = validate_manifest(read_json(source / "manifest.json"))
    gold = validate_gold(read_json(args.gold), manifest)
    run = read_json(source / "run.json")
    require(run.get("completed") is True, "incomplete run: finish or recover inference before scoring")
    require(run["manifest_sha256"] == json_hash(manifest), "run manifest hash mismatch")
    records = []
    expected_hashes = {c["id"]: c["call_sha256"] for c in run["cases"]}
    for case in manifest["cases"]:
        path = source / "calls" / f"{case['id']}.json"
        require(digest(path) == expected_hashes.get(case["id"]), "raw call changed after inference; restore original calls before scoring")
        record = read_json(path)
        require(record["case_id"] == case["id"], "case id mismatch")
        if record["status"] == "ok":
            # Score raw output again, never the editable predictions snapshot.
            record["prediction"] = prediction_to_page(parse_prediction(record["raw"], case), case, record["images"])
        else:
            record["prediction"] = None
        records.append(record)
    out = new_directory(args.output)
    threshold = run["config"]["iou_threshold"]
    write_metrics(out, records, manifest, gold, threshold)
    write_json(out / "scoring_provenance.json", {"run_json_sha256": digest(source / "run.json"),
               "gold_file_sha256": digest(args.gold), "code": code_provenance(),
               "iou_threshold": threshold, "note": "Threshold copied from the original run; no post-hoc threshold override."})
    print(f"Scored results: {out / 'metrics.json'}")


def pack(args):
    source = args.run.expanduser().resolve()
    require((source / "run.json").is_file(), "not a run directory")
    destination = args.output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(not destination.exists(), "archive exists; choose a new output path")
    scored = args.scored_dir.expanduser().resolve() if args.scored_dir else None
    if scored:
        proof = read_json(scored / "scoring_provenance.json")
        require(proof["run_json_sha256"] == digest(source / "run.json"), "scored metrics belong to another run")
    allowed = {"calls", "predictions"} | ({"images"} if args.include_images else set())
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            top_files = {"run.json", "manifest.json", "metrics.json", "metrics.csv", "metrics.txt"}
            if path.is_file() and not path.is_symlink() and ((len(relative.parts) == 1 and path.name in top_files) or relative.parts[0] in allowed):
                if path != destination:
                    archive.write(path, relative)
        if scored:
            for name in ("metrics.json", "metrics.csv", "metrics.txt", "scoring_provenance.json"):
                archive.write(scored / name, "scored/" + name)
    print(f"Created {destination}; images={'included' if args.include_images else 'excluded'}, source PDFs excluded.")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create local manifest and unannotated gold template")
    init.add_argument("pdf", type=Path)
    init.add_argument("--pages", nargs="+", type=int, required=True)
    init.add_argument("--document-id", default="D01")
    init.add_argument("--region-id", default="whole-page")
    init.add_argument("--bbox", nargs=4, type=float, default=[0., 0., 1., 1.])
    init.add_argument("--output", type=Path, required=True)
    init.set_defaults(func=initialize)
    run = commands.add_parser("run", help="infer without ever loading gold")
    run.add_argument("manifest", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--backend", choices=["transformers", "openai", "replay"], default="transformers")
    run.add_argument("--model-id", default="Qwen/Qwen3.5-9B")
    run.add_argument("--revision")
    run.add_argument("--online", action="store_true", help="allow HF model download (default: cached/local only)")
    run.add_argument("--max-pixels", type=int, default=786432, help="total rendered pixels per call, shared by all images")
    run.add_argument("--long-edge", type=int, default=1600, help="maximum edge of each rendered crop")
    run.add_argument("--max-input-tokens", type=int, default=8192, help="enforced by Transformers backend only")
    run.add_argument("--max-new-tokens", type=int, default=3072)
    run.add_argument("--seed", type=int, default=17)
    run.add_argument("--iou-threshold", type=float, default=.5, help="freeze before looking at predictions")
    run.add_argument("--responses-dir", type=Path, help="original run directory for replay")
    run.add_argument("--api-url", help="explicit local OpenAI-compatible server base URL ending in /v1")
    run.add_argument("--api-extra", type=json.loads, default={}, help='extra server fields, e.g. {"chat_template_kwargs":{"enable_thinking":false}}')
    run.add_argument("--api-timeout", type=float, default=600)
    run.add_argument("--server-quantization", help="record actual server quantization, e.g. GGUF Q4_K_M")
    run.set_defaults(func=execute)
    scoring = commands.add_parser("score", help="score an immutable raw run against separately reviewed gold")
    scoring.add_argument("run", type=Path)
    scoring.add_argument("--gold", type=Path, required=True)
    scoring.add_argument("--output", type=Path, required=True)
    scoring.set_defaults(func=score)
    packing = commands.add_parser("pack", help="package local outputs to send back for analysis")
    packing.add_argument("run", type=Path)
    packing.add_argument("--output", type=Path, required=True)
    packing.add_argument("--include-images", action="store_true")
    packing.add_argument("--scored-dir", type=Path, help="also include separately scored metrics for this exact run")
    packing.set_defaults(func=pack)
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.command == "run":
        require(65536 <= args.max_pixels <= 16000000, "max-pixels must be 65536..16000000")
        require(256 <= args.long_edge <= 6000, "long-edge must be 256..6000")
        require(0 < args.iou_threshold <= 1, "iou-threshold must be in (0,1]")
        require(args.max_new_tokens > 0 and args.max_input_tokens > 0 and args.api_timeout > 0, "budgets/timeouts must be positive")
        require(isinstance(args.api_extra, dict) and not ({"model", "messages", "max_tokens", "temperature", "seed", "stream"} & set(args.api_extra)),
                "api-extra must be an object and cannot override recorded generation/image fields")
    return args.func(args) or 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
