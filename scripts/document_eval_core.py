"""Strict perception evaluation. No GPU imports, fuzzy names, or inferred gold."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

VERSION = "document-eval-0.2"
RELATIONS = {"cable", "circuit", "fiber_allocation", "wiring", "power", "unknown"}
ROLES = {"main_diagram", "inset", "legend", "table", "notes", "mixed", "unknown"}
STATES = {"visible", "ambiguous", "unreadable", "not_provided", "not_applicable"}
ATTRIBUTES = {"cable_type", "cores", "length", "construction", "rate"}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_bad_constant,
                      object_pairs_hook=_unique_keys)


def _bad_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def require(ok, message):
    if not ok:
        raise ValueError(message)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def box_valid(box):
    return (isinstance(box, list) and len(box) == 4 and all(number(x) for x in box)
            and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value))


def text(value):
    return isinstance(value, str) and bool(value.strip())


def normalize_name(value):
    # Deliberately preserve punctuation, case, digits and Chinese characters.
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value))


def iou(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - area
    return area / union if union else 0


def match_objects(predicted, gold, threshold):
    """Maximum cardinality FIRST, then maximum IoU; never match using names/types."""
    if not predicted or not gold:
        return []
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    overlaps = np.array([[iou(p["bbox"], g["bbox"]) for g in gold] for p in predicted])
    # A bonus larger than the total possible IoU difference enforces cardinality.
    bonus = min(len(predicted), len(gold)) + 1
    weights = np.where(overlaps >= threshold, bonus + overlaps, 0.)
    rows, cols = linear_sum_assignment(-weights)
    return [(int(p), int(g), float(overlaps[p, g])) for p, g in zip(rows, cols)
            if overlaps[p, g] >= threshold]


def validate_manifest(manifest):
    require(manifest.get("schema_version") == "1.0", "manifest schema_version must be 1.0")
    require(isinstance(manifest.get("documents"), list) and manifest["documents"], "documents required")
    ids = set()
    for doc in manifest["documents"]:
        require(identifier(doc.get("id")) and doc["id"] not in ids, "unique safe document ids required")
        ids.add(doc["id"])
        require(text(doc.get("path")), "PDF path required")
        require(bool(re.fullmatch(r"[a-f0-9]{64}", doc.get("sha256", ""))), "PDF sha256 required")
    require(isinstance(manifest.get("cases"), list) and manifest["cases"], "cases required")
    case_ids = set()
    for case in manifest["cases"]:
        require(case.get("task_type", "diagram") in {"text", "table", "diagram"}, "invalid task_type")
        require(identifier(case.get("id")) and case["id"] not in case_ids, "unique safe case ids required")
        case_ids.add(case["id"])
        require(case.get("document_id") in ids, "unknown document_id")
        require(type(case.get("page")) is int and case["page"] > 0, "physical PDF page is 1-based")
        require(identifier(case.get("region_id")), "safe region_id required")
        require(box_valid(case.get("target_bbox")), "target_bbox must be normalized whole-page coordinates")
        require(type(case.get("rotation_degrees", 0)) is int and case.get("rotation_degrees", 0) in {0, 90, 180, 270}, "invalid extra rotation")
        require(case.get("condition") in {"A", "B", "C"}, "condition must be A/B/C")
        contexts = case.get("contexts", [])
        require(isinstance(contexts, list) and len(contexts) <= 3, "at most 3 context images per case")
        require(case["condition"] != "A" or not contexts, "A may only contain the target image")
        require(case["condition"] == "A" or contexts, "B/C need explicit context images")
        require(case["condition"] != "C" or case.get("context_selection") == "oracle_diagnostic",
                "C must be labeled oracle_diagnostic; automatic retrieval is not implemented")
        for context in contexts:
            require(type(context.get("page")) is int and context["page"] > 0, "context page required")
            require(box_valid(context.get("bbox")), "context bbox required")
            require(type(context.get("rotation_degrees", 0)) is int and context.get("rotation_degrees", 0) in {0, 90, 180, 270}, "invalid context rotation")
            require(text(context.get("purpose")), "state context purpose/applicability")
        items = case.get("items", [])
        require(isinstance(items, list), "items must be a list")
        item_ids = set()
        for item in items:
            require(identifier(item.get("id")) and item["id"] not in item_ids, "unique item ids required")
            require(text(item.get("question")), "item question required")
            item_ids.add(item["id"])
    return manifest


def validate_value(value):
    if value is None or isinstance(value, (str, bool)) or number(value):
        return
    require(isinstance(value, dict) and set(value) == {"value", "unit"}
            and number(value["value"]) and text(value["unit"]), "value must be scalar or {value,unit}")


def validate_prediction(data, case):
    if case.get("task_type", "diagram") != "diagram":
        from document_eval_tasks import validate_task_prediction
        return validate_task_prediction(data, case)
    require(isinstance(data, dict), "prediction must be an object")
    require(set(data) == {"region_role", "objects", "connections", "answers", "uncertainties"},
            "prediction has missing or unexpected top-level fields")
    require(data["region_role"] in ROLES, "invalid region_role")
    for field in ("objects", "connections", "answers", "uncertainties"):
        require(isinstance(data[field], list), f"{field} must be an array")
    require(all(text(x) for x in data["uncertainties"]), "uncertainties must be strings")
    ids = set()
    for obj in data["objects"]:
        require(isinstance(obj, dict) and {"id", "bbox", "kind", "name", "evidence"} <= set(obj)
                and set(obj) <= {"id", "bbox", "kind", "name", "evidence", "parent_id"}, "invalid object fields")
        require(identifier(obj["id"]) and obj["id"] not in ids, "duplicate/unsafe object id")
        ids.add(obj["id"])
        require(box_valid(obj["bbox"]), "invalid object bbox")
        require(text(obj["kind"]), "object kind required")
        require(obj["name"] is None or text(obj["name"]), "object name must be text/null")
        validate_evidence(obj["evidence"])
    edges = set()
    parents = {o["id"]: o.get("parent_id") for o in data["objects"]}
    for key, parent in parents.items():
        seen = {key}
        while parent:
            require(parent in parents and parent not in seen, "invalid/cyclic parent_id")
            seen.add(parent)
            parent = parents[parent]
    for edge in data["connections"]:
        require(isinstance(edge, dict) and {"id", "source", "target", "relation", "directed", "attributes", "evidence"} <= set(edge)
                and set(edge) <= {"id", "source", "target", "relation", "directed", "attributes", "evidence", "attribute_evidence"}, "invalid connection fields")
        require(identifier(edge["id"]) and edge["id"] not in edges, "duplicate/unsafe connection id")
        edges.add(edge["id"])
        require(edge["source"] in ids and edge["target"] in ids, "dangling connection endpoint")
        require(edge["relation"] in RELATIONS and type(edge["directed"]) is bool, "invalid relation/direction")
        require(isinstance(edge["attributes"], dict) and set(edge["attributes"]) <= ATTRIBUTES, "invalid attributes")
        for value in edge["attributes"].values():
            validate_value(value)
        validate_evidence(edge["evidence"])
        field_evidence = edge.get("attribute_evidence", {})
        require(isinstance(field_evidence, dict) and set(field_evidence) <= set(edge["attributes"]), "invalid attribute evidence keys")
        for refs in field_evidence.values():
            validate_evidence(refs)
    asked = {x["id"] for x in case.get("items", [])}
    answered = set()
    for answer in data["answers"]:
        require(isinstance(answer, dict) and set(answer) == {"id", "state", "value", "reason", "evidence"}, "invalid answer fields")
        require(answer["id"] in asked and answer["id"] not in answered, "unknown/duplicate answer id")
        answered.add(answer["id"])
        require(answer["state"] in {"answered", "abstained"}, "invalid answer state")
        validate_value(answer["value"])
        require((answer["value"] is not None) == (answer["state"] == "answered"), "answer state/value conflict")
        require(isinstance(answer["reason"], str), "answer reason must be text")
        validate_evidence(answer["evidence"])
    # Missing items are omitted answers, not execution errors or correct abstentions.
    return data


def validate_evidence(rows):
    require(isinstance(rows, list), "evidence must be an array")
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"image_id", "bbox"}, "invalid evidence fields")
        require(text(row["image_id"]) and box_valid(row["bbox"]), "invalid evidence pointer")


def parse_prediction(raw, case):
    # Preserve raw text; do not silently repair fences, missing brackets or endpoints.
    data = json.loads(raw, parse_constant=_bad_constant, object_pairs_hook=_unique_keys)
    return validate_prediction(data, case)


def evidence_counts(prediction, image_ids):
    pointers = list(prediction.get("evidence", []))
    pointers += [e for collection in ("objects", "connections", "answers", "cells")
                 for row in prediction.get(collection, []) for e in row["evidence"]]
    pointers += [e for row in prediction.get("connections", []) for refs in row.get("attribute_evidence", {}).values() for e in refs]
    return {"references": len(pointers), "valid_references": sum(e["image_id"] in image_ids for e in pointers)}


def prf(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp/(tp+fp) if tp+fp else None,
            "recall": tp/(tp+fn) if tp+fn else None,
            "f1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}


def ratio(correct, total):
    return {"correct": correct, "total": total, "accuracy": correct/total if total else None}


def comparable(value):
    if isinstance(value, str):
        return normalize_name(value)
    if isinstance(value, dict):
        factors = {"m": ("length", 1), "km": ("length", 1000), "米": ("length", 1), "千米": ("length", 1000),
                   "Mbps": ("rate", 1), "Gbps": ("rate", 1000), "Mbit/s": ("rate", 1), "Gbit/s": ("rate", 1000)}
        if set(value) == {"value", "unit"} and value["unit"] in factors and number(value["value"]):
            dimension, scale = factors[value["unit"]]
            return (dimension, value["value"] * scale)
        return value
    return value


def value_equal(predicted, expected, tolerance=0):
    a, b = comparable(predicted), comparable(expected)
    if isinstance(a, tuple) and isinstance(b, tuple):
        return a[0] == b[0] and abs(a[1]-b[1]) <= tolerance
    if number(a) and number(b):
        return abs(a-b) <= tolerance
    return type(a) is type(b) and a == b


def validate_gold(gold, manifest):
    if gold.get("schema_version") == "2.0":
        from document_eval_tasks import validate_gold_tasks
        return validate_gold_tasks(gold, manifest)
    require(gold.get("schema_version") == "1.0", "gold schema_version must be 1.0")
    require(gold.get("manifest_sha256") == json_hash(manifest), "gold belongs to a different manifest")
    require(gold.get("annotation_status") == "reviewed", "gold must be independently reviewed")
    require(text(gold.get("reviewer")), "reviewer identifier required")
    require(isinstance(gold.get("cases"), dict), "gold cases required")
    known_cases = {c["id"]: c for c in manifest["cases"]}
    require(set(gold["cases"]) <= set(known_cases), "unknown gold case id")
    require(any(x is not None for x in gold["cases"].values()), "gold contains no annotations")
    for cid, g in gold["cases"].items():
        if g is None:
            continue
        require(g.get("complete") is True, f"{cid}: incomplete graph cannot be scored for recall")
        require(g.get("region_role") in ROLES - {"unknown"}, "gold region role required")
        require(isinstance(g.get("objects"), list) and isinstance(g.get("connections"), list), "gold graph arrays required")
        ids = set()
        for obj in g["objects"]:
            require(identifier(obj.get("id")) and obj["id"] not in ids and box_valid(obj.get("bbox")), "invalid gold object")
            ids.add(obj["id"])
            require(text(obj.get("kind")), "gold object kind required")
            require(obj.get("name") is None or text(obj["name"]), "invalid gold name")
        edge_ids = set()
        for edge in g["connections"]:
            require(identifier(edge.get("id")) and edge["id"] not in edge_ids, "invalid gold connection id")
            edge_ids.add(edge["id"])
            require(edge.get("source") in ids and edge.get("target") in ids, "invalid gold endpoint")
            require(edge.get("relation") in RELATIONS - {"unknown"} and type(edge.get("directed")) is bool, "invalid gold relation")
            require(isinstance(edge.get("attributes"), dict) and set(edge["attributes"]) <= ATTRIBUTES, "invalid gold attributes")
            for spec in edge["attributes"].values():
                validate_fact(spec)
        require(isinstance(g.get("items"), dict) and set(g["items"]) == {x["id"] for x in known_cases[cid].get("items", [])},
                "gold must label every predeclared item, including unknown states")
        for spec in g["items"].values():
            validate_fact(spec)
    return gold


def validate_fact(spec):
    require(isinstance(spec, dict) and spec.get("state") in STATES, "invalid gold fact state")
    validate_value(spec.get("value"))
    require((spec.get("value") is not None) == (spec["state"] == "visible"), "gold state/value conflict")
    require(number(spec.get("tolerance", 0)) and spec.get("tolerance", 0) >= 0, "invalid numeric tolerance")


def edge_key(edge, mapping=None):
    a, b = edge["source"], edge["target"]
    if mapping is not None:
        if a not in mapping or b not in mapping:
            return None
        a, b = mapping[a], mapping[b]
    if not edge["directed"]:
        a, b = sorted((a, b))
    return a, b, edge["relation"], edge["directed"]


def score_case(prediction, gold, threshold):
    if gold.get("task_type", "diagram") != "diagram":
        from document_eval_tasks import score_task
        return score_task(prediction, gold)
    # Invalid JSON/execution errors become empty output for end-to-end recall.
    pred = prediction or {"objects": [], "connections": [], "answers": [], "region_role": "unknown"}
    pairs = match_objects(pred["objects"], gold["objects"], threshold)
    mapping = {pred["objects"][p]["id"]: gold["objects"][g]["id"] for p, g, _ in pairs}
    node_result = prf(len(pairs), len(pred["objects"])-len(pairs), len(gold["objects"])-len(pairs))
    kind_correct = name_correct = name_total = parent_correct = parent_total = 0
    for p, g, _ in pairs:
        po, go = pred["objects"][p], gold["objects"][g]
        kind_correct += po["kind"] == go["kind"]
        if go.get("name") is not None:
            name_total += 1
            name_correct += po["name"] is not None and normalize_name(po["name"]) == normalize_name(go["name"])
        if go.get("parent_id") is not None:
            parent_total += 1
            parent_correct += mapping.get(po.get("parent_id")) == go["parent_id"]
    pred_groups, gold_groups = defaultdict(list), defaultdict(list)
    for edge in pred["connections"]:
        pred_groups[edge_key(edge, mapping)].append(edge)
    for edge in gold["connections"]:
        gold_groups[edge_key(edge)].append(edge)
    relation_counts = {k: Counter(tp=0, fp=0, fn=0) for k in RELATIONS}
    matches, skipped_parallel = [], 0
    for key in set(pred_groups) | set(gold_groups):
        ps, gs = pred_groups[key], gold_groups[key]
        count = min(len(ps), len(gs)) if key is not None else 0
        for edge in ps:
            relation_counts[edge["relation"]]["fp"] += 1
        for edge in gs:
            relation_counts[edge["relation"]]["fn"] += 1
        if count:
            rel = key[2]
            relation_counts[rel].update(tp=count, fp=-count, fn=-count)
            if len(ps) == len(gs) == 1:
                matches.append((ps[0], gs[0]))
            else:
                # Endpoints alone cannot identify which parallel line owns a label.
                skipped_parallel += count
    totals = sum(relation_counts.values(), Counter())
    attrs = {}
    paired = {g["id"]: p for p, g in matches}
    parallel_gold = {e["id"] for key, edges in gold_groups.items()
                     if len(edges) > 1 or len(pred_groups[key]) > 1 for e in edges}
    for field in sorted(ATTRIBUTES):
        total = conditional_total = correct = unknown_claims = 0
        for edge in gold["connections"]:
            spec = edge["attributes"].get(field)
            if spec is None or edge["id"] in parallel_gold:
                continue
            prediction_edge = paired.get(edge["id"])
            val = prediction_edge["attributes"].get(field) if prediction_edge else None
            if spec["state"] == "visible":
                total += 1
                conditional_total += prediction_edge is not None
                correct += val is not None and value_equal(val, spec["value"], spec.get("tolerance", 0))
            elif spec["state"] != "not_applicable":
                unknown_claims += val is not None
        attrs[field] = {"conditional": ratio(correct, conditional_total), "end_to_end": ratio(correct, total),
                        "claims_on_unknown_gold": unknown_claims}
    answers = {a["id"]: a for a in pred["answers"]}
    eligible = answered = wrong = known = known_answered = unknown = unknown_abstained = omitted = 0
    for key, spec in gold["items"].items():
        if spec["state"] == "not_applicable":
            continue
        eligible += 1
        answer = answers.get(key)
        omitted += answer is None
        did_answer = answer is not None and answer["state"] == "answered"
        answered += did_answer
        if spec["state"] == "visible":
            known += 1
            known_answered += did_answer
            wrong += did_answer and not value_equal(answer["value"], spec["value"], spec.get("tolerance", 0))
        else:
            unknown += 1
            unknown_abstained += answer is not None and answer["state"] == "abstained"
            wrong += did_answer
    return {"objects": node_result, "object_kind_conditional": ratio(kind_correct, len(pairs)),
            "object_parent_conditional": ratio(parent_correct, parent_total),
            "object_name_conditional": ratio(name_correct, name_total),
            "object_name_end_to_end": ratio(name_correct, sum(o.get("name") is not None for o in gold["objects"])),
            "region_role": ratio(int(pred["region_role"] == gold["region_role"]), 1),
            "connections": prf(totals["tp"], totals["fp"], totals["fn"]),
            "connections_by_relation": {k: prf(v["tp"], v["fp"], v["fn"]) for k, v in sorted(relation_counts.items()) if sum(v.values())},
            "attributes": attrs, "parallel_matches_without_attribute_score": skipped_parallel,
            "selective": {"eligible": eligible, "answered": answered, "wrong": wrong,
                          "coverage": answered/eligible if eligible else None,
                          "selective_error": wrong/answered if answered else None,
                          "known": known, "known_answered": known_answered,
                          "known_answer_coverage": known_answered/known if known else None,
                          "unknown": unknown, "unknown_explicit_abstentions": unknown_abstained, "omitted": omitted},
            "object_matches": [{"prediction_id": pred["objects"][p]["id"], "gold_id": gold["objects"][g]["id"], "iou": overlap} for p, g, overlap in pairs]}
