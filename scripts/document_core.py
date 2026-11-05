"""Schema validation and conservative provenance-preserving document aggregation."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
FIELDS = json.loads((ROOT / "config/business-fields.json").read_text())


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["项目", "光缆", "光路", "非保护业务", "保护业务"]
    fields: dict[str, str | int | float | None]
    evidence: str
    uncertainties: list[str] = Field(default_factory=list)


class PageExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_kind: str
    title: str | None
    records: list[Observation]
    unresolved: list[str]


def parse_json(raw):
    text = raw.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    return json.loads(text)


def validate_page(raw):
    decoded = parse_json(raw)
    notes = []
    # The prompt permits an empty array but does not constrain uncertainty items to strings.
    # Preserve structured uncertainty as JSON text for the display schema, never discard facts.
    if isinstance(decoded, dict):
        arrays = [(decoded, "unresolved")]
        if isinstance(decoded.get("records"),list):
            arrays += [(row, "uncertainties") for row in decoded["records"] if isinstance(row,dict)]
        for obj, key in arrays:
            if isinstance(obj.get(key),list):
                if any(isinstance(v,(dict,list)) for v in obj[key]):
                    obj[key] = [json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for v in obj[key]]
                    notes.append(f"{key}: structured uncertainty preserved as JSON text")
    parsed = PageExtraction.model_validate(decoded)
    for row in parsed.records:
        unknown = set(row.fields) - set(FIELDS[row.category])
        if unknown:
            raise ValueError(f"Unknown fields for {row.category}: {sorted(unknown)}")
    return {**parsed.model_dump(), "format_normalization": notes}


def value_key(value):
    # Only whitespace and numeric JSON representation are normalized, never station names.
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return str(float(value))
    return "".join(str(value).split())


def missing_value(value):
    if value is None:
        return True
    if not isinstance(value,str):
        return False
    return "".join(value.split()).casefold() in {
        "", "null", "none", "nonespecified", "未明确", "未注明", "未给出", "未知", "不详", "未明确具体速率"
    }


def aggregate(observations, proposal):
    """Model only proposes ID groups. It cannot invent merged field values."""
    indexed = {r["id"]: r for r in observations}
    used, groups, rejected = set(), [], []
    for group in proposal.get("groups", []):
        ids = group.get("source_ids", []) if isinstance(group, dict) else []
        if (not isinstance(ids, list) or len(ids) < 2 or not all(isinstance(i, str) for i in ids)
                or len(set(ids)) != len(ids) or not set(ids) <= indexed.keys()
                or set(ids) & used or len({indexed[i]["category"] for i in ids}) != 1):
            rejected.append({"proposal": group, "reason": "invalid, reused or mixed-category IDs"})
            continue
        groups.append((ids, group.get("reason", "")))
        used.update(ids)
    # Preserve every record that the merger omitted or incorrectly referenced.
    groups.extend(([r["id"]], "not merged") for r in observations if r["id"] not in used)
    output = []
    for number, (ids, reason) in enumerate(groups, 1):
        source = [indexed[i] for i in ids]
        category = source[0]["category"]
        fields, conflicts, provenance = {}, {}, {}
        for name in FIELDS[category]:
            variants = {}
            for row in source:
                value = row["fields"].get(name)
                if missing_value(value):
                    continue
                key = value_key(value)
                entry = variants.setdefault(key, {"value": value, "source_ids": []})
                entry["source_ids"].append(row["id"])
            if len(variants) == 1:
                entry = next(iter(variants.values()))
                fields[name], provenance[name] = entry["value"], entry["source_ids"]
            else:
                fields[name] = None
                if variants:
                    conflicts[name] = list(variants.values())
        output.append({"id": f"R{number:04d}", "category": category, "fields": fields,
                       "source_ids": ids, "source_pages": sorted({r["page"] for r in source}),
                       "field_sources": provenance, "conflicts": conflicts,
                       "merge_reason": reason, "status": "冲突待复核" if conflicts else "未人工确认",
                       "evidence": [{"source_id": r["id"], "page": r["page"], "text": r["evidence"]} for r in source]})
    links = []
    for link in proposal.get("links", []):
        if (isinstance(link, dict) and isinstance(link.get("from_id"), str)
                and isinstance(link.get("to_id"), str) and link["from_id"] in indexed
                and link["to_id"] in indexed and link["from_id"] != link["to_id"]):
            links.append({**link, "status": "模型候选关联，未验证"})
        else:
            rejected.append({"proposal": link, "reason": "invalid link reference"})
    return {"records": output, "links": links, "rejected_proposals": rejected,
            "normalization": "explicit_unknown_markers_to_null; raw observations unchanged"}
