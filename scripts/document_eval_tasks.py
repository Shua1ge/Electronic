"""Text/table task contracts and metrics, separate from drawing perception."""
from __future__ import annotations

from document_eval_core import (STATES, normalize_name, prf, ratio, require,
                                validate_evidence, validate_fact, validate_prediction,
                                value_equal)


def validate_answers(answers, case):
    # Reuse the graph contract's strict answer validation without exposing gold.
    empty = {"region_role": "unknown", "objects": [], "connections": [],
             "answers": answers, "uncertainties": []}
    validate_prediction(empty, {**case, "task_type": "diagram"})


def validate_task_prediction(data, case):
    kind = case["task_type"]
    require(isinstance(data, dict), "prediction must be an object")
    common = {"answers", "uncertainties"}
    fields = common | ({"transcript", "state", "evidence"} if kind == "text" else {"rows", "cols", "header_rows", "cells"})
    require(set(data) == fields, "missing/unexpected task output fields")
    require(isinstance(data["uncertainties"], list) and all(isinstance(x, str) for x in data["uncertainties"]), "uncertainties must be strings")
    validate_answers(data["answers"], case)
    if kind == "text":
        require(data["state"] in {"answered", "abstained"}, "invalid transcript state")
        require((isinstance(data["transcript"], str) and len(data["transcript"]) <= 30000) if data["state"] == "answered"
                else data["transcript"] is None, "transcript state/value conflict")
        validate_evidence(data["evidence"])
    else:
        from document_annotation import validate_table
        require(isinstance(data["cells"], list), "cells array required")
        converted = []
        for i, cell in enumerate(data["cells"]):
            require(isinstance(cell, dict) and set(cell) == {"row", "col", "rowspan", "colspan", "text", "state", "evidence"}, "invalid table cell fields")
            require(cell["state"] in {"answered", "abstained"}, "invalid cell state")
            require(isinstance(cell["text"], str) if cell["state"] == "answered" else cell["text"] is None, "cell state/value conflict")
            validate_evidence(cell["evidence"])
            converted.append({**cell, "id": f"c{i+1}", "text": cell["text"] or "",
                              "state": "visible" if cell["state"] == "answered" else "unreadable"})
        validate_table({**data, "cells": converted})
    return data


def levenshtein(a, b):
    try:
        from rapidfuzz.distance import Levenshtein
        return Levenshtein.distance(a, b)
    except ImportError:
        if len(a) < len(b):
            a, b = b, a
        previous = list(range(len(b)+1))
        for i, x in enumerate(a, 1):
            current = [i]
            for j, y in enumerate(b, 1):
                current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(x != y)))
            previous = current
        return previous[-1]


def character_counts(predicted, expected):
    a, b = normalize_name(predicted), normalize_name(expected)
    return {"edits": levenshtein(a, b), "reference_characters": len(b), "exact": int(a == b), "samples": 1}


def character_summary(counts):
    return {**counts, "cer": counts["edits"]/counts["reference_characters"] if counts["reference_characters"] else None,
            "exact_match": counts["exact"]/counts["samples"] if counts["samples"] else None,
            "normalization": "Unicode NFC; remove whitespace only; preserve case, punctuation and digits"}


def score_answers(predicted, items):
    answers = {a["id"]: a for a in predicted}
    counts = dict(eligible=0, answered=0, wrong=0, known=0, known_answered=0,
                  unknown=0, unknown_explicit_abstentions=0, omitted=0)
    for key, spec in items.items():
        if spec["state"] == "not_applicable":
            continue
        counts["eligible"] += 1
        answer = answers.get(key)
        counts["omitted"] += answer is None
        did_answer = answer is not None and answer["state"] == "answered"
        counts["answered"] += did_answer
        if spec["state"] == "visible":
            counts["known"] += 1
            counts["known_answered"] += did_answer
            counts["wrong"] += did_answer and not value_equal(answer["value"], spec["value"], spec.get("tolerance", 0))
        else:
            counts["unknown"] += 1
            counts["unknown_explicit_abstentions"] += answer is not None and answer["state"] == "abstained"
            counts["wrong"] += did_answer
    return selective_summary(counts)


def selective_summary(counts):
    return {**counts, "coverage": counts["answered"]/counts["eligible"] if counts["eligible"] else None,
            "selective_error": counts["wrong"]/counts["answered"] if counts["answered"] else None,
            "known_answer_coverage": counts["known_answered"]/counts["known"] if counts["known"] else None}


def score_task(prediction, gold):
    kind = gold["task_type"]
    result = {"task_type": kind, "selective": score_answers(prediction["answers"] if prediction else [], gold["items"])}
    if kind == "text":
        value = prediction["transcript"] if prediction and prediction["state"] == "answered" else ""
        counts = character_counts(value, gold["transcript"]) if gold["state"] == "visible" else dict(edits=0, reference_characters=0, exact=0, samples=0)
        if not prediction or prediction["state"] != "answered":
            counts["exact"] = 0
        result["transcription"] = character_summary(counts)
    else:
        key = lambda c: (c["row"], c["col"], c["rowspan"], c["colspan"])
        ps = {key(c): c for c in prediction["cells"]} if prediction else {}
        gs = {key(c): c for c in gold["cells"]}
        matched = set(ps) & set(gs)
        result["cell_structure"] = prf(len(matched), len(set(ps)-matched), len(set(gs)-matched))
        result["dimensions"] = ratio(int(bool(prediction) and all(prediction[k] == gold[k] for k in ("rows", "cols", "header_rows"))), 1)
        counts = dict(edits=0, reference_characters=0, exact=0, samples=0)
        for k, cell in gs.items():
            if cell["state"] != "visible":
                continue
            candidate = ps.get(k)
            value = candidate["text"] if candidate and candidate["state"] == "answered" else ""
            row = character_counts(value, cell["text"])
            for field in counts:
                counts[field] += row[field]
            # Missing empty gold cells must not be credited as exact.
            if candidate is None or candidate["state"] != "answered":
                counts["exact"] -= row["exact"]
        result["cell_text_end_to_end"] = character_summary(counts)
    return result


def aggregate_tasks(results):
    kind = results[0]["task_type"]
    result = {"task_type": kind}
    character_field = "transcription" if kind == "text" else "cell_text_end_to_end"
    result[character_field] = character_summary({k: sum(r[character_field][k] for r in results)
                                                for k in ("edits", "reference_characters", "exact", "samples")})
    if kind == "table":
        result["cell_structure"] = prf(*(sum(r["cell_structure"][k] for r in results) for k in ("tp", "fp", "fn")))
        result["dimensions"] = ratio(sum(r["dimensions"]["correct"] for r in results), len(results))
    count_keys = ("eligible", "answered", "wrong", "known", "known_answered", "unknown", "unknown_explicit_abstentions", "omitted")
    result["selective"] = selective_summary({k: sum(r["selective"][k] for r in results) for k in count_keys})
    return result


def validate_gold_tasks(gold, manifest):
    from document_annotation import validate_table
    from document_eval_core import json_hash, text
    require(gold.get("annotation_status") == "reviewed" and text(gold.get("reviewer")), "需要已复核的人工真值及复核人")
    require(gold.get("manifest_sha256") == json_hash(manifest), "gold belongs to another manifest")
    cases = {c["id"]: c for c in manifest["cases"]}
    require(isinstance(gold.get("cases"), dict) and set(gold["cases"]) <= set(cases), "gold case ids invalid")
    require(any(v is not None for v in gold["cases"].values()), "没有可评分真值")
    for cid, g in gold["cases"].items():
        if g is None:
            continue
        require(g.get("complete") is True and g.get("task_type") == cases[cid].get("task_type", "diagram"), "gold task mismatch/incomplete")
        require(isinstance(g.get("items"), dict) and set(g["items"]) == {x["id"] for x in cases[cid].get("items", [])}, "需标注全部预定义判断项")
        for value in g["items"].values():
            validate_fact(value)
        if g["task_type"] == "text":
            require(g.get("state") in STATES and isinstance(g.get("transcript"), str), "正文真值无效")
        elif g["task_type"] == "table":
            validate_table(g)
        else:
            from document_eval_core import validate_gold
            legacy = {**gold, "schema_version": "1.0", "cases": {cid: g}}
            validate_gold(legacy, manifest)
    return gold
