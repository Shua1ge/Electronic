"""Score a separate, explicit spot-check file. Never supplies labels to inference."""
import json
import re
import sys
from collections import Counter
from pathlib import Path
import networkx as nx

from document_core import FIELDS, parse_json


def norm(value):
    return "".join(str(value or "").split()).replace("—", "-").replace("－", "-")


def matches(pattern, value):
    return bool(re.search(pattern, norm(value), re.IGNORECASE))


def case_matches(case, row):
    if row["category"] != case["category"]:
        return False
    if not set(case["source_pages"]) & set(row.get("source_pages", [row.get("page")])):
        return False
    fields = row["fields"]
    if "endpoints" in case:
        a,z=fields.get("A端站点"),fields.get("Z端站点")
        x,y=case["endpoints"]
        if not ((matches(x,a) and matches(y,z)) or (matches(x,z) and matches(y,a))):
            return False
    if "context" in case and not matches(case["context"],json.dumps(fields,ensure_ascii=False)):
        return False
    if not all(matches(pattern, fields.get(key)) for key,pattern in case.get("fields",{}).items()):
        return False
    if "pair_fields" in case:
        a,z=[fields.get(k) for k in case["pair_fields"]]
        x,y=case["pair_patterns"]
        if not ((matches(x,a) and matches(y,z)) or (matches(x,z) and matches(y,a))):
            return False
    return True


def recover_complete_records(raw):
    """Audit-only recovery of complete objects from a truncated records array.

    No missing braces or field values are invented; incomplete last object is discarded.
    This never turns a failed page into a successful pipeline result.
    """
    match=re.search(r'"records"\s*:\s*\[',raw)
    if not match:
        return []
    pos=match.end(); decoder=json.JSONDecoder(); rows=[]
    while pos<len(raw):
        while pos<len(raw) and raw[pos] in ' \n\r\t,':
            pos+=1
        if pos==len(raw) or raw[pos]==']':
            break
        try:
            obj,end=decoder.raw_decode(raw,pos)
        except ValueError:
            break
        if not isinstance(obj,dict):
            break
        rows.append(obj);pos=end
    return rows


def audit(out):
    out=Path(out)
    protocol=json.loads((out/"audit/protocol.json").read_text())
    run=json.loads((out/"run.json").read_text())
    structured=json.loads((out/"structured.json").read_text())
    pages=[json.loads(p.read_text()) for p in sorted((out/"pages").glob("*.json"))]
    # For failed pages, inspect the last syntactically valid response without changing outputs.
    readable=[]
    for page in pages:
        e=page["extraction"]
        if e is None:
            for attempt in reversed(page["attempts"]):
                try:
                    candidate=parse_json(attempt["raw"])
                    if isinstance(candidate,dict) and isinstance(candidate.get("records"),list):
                        e=candidate
                        break
                except (ValueError,TypeError):
                    pass
            if e is None and page["attempts"]:
                recovered=recover_complete_records(page["attempts"][-1]["raw"])
                e={"records":recovered}
        for number,row in enumerate((e or {}).get("records",[]),1):
            if not isinstance(row,dict) or row.get("category") not in FIELDS or not isinstance(row.get("fields"),dict):
                continue
            readable.append({**row,"id":f"P{page['page']:03d}R{number:03d}","page":page["page"],
                             "included_in_strict_pipeline":page["extraction"] is not None})
    checks=[]
    (out/"audit/raw-readable.json").write_text(json.dumps(readable,ensure_ascii=False,indent=2)+"\n")
    for case in protocol["checks"]:
        checks.append({**case,
                       "raw_matching_ids":[r["id"] for r in readable if case_matches(case,r)],
                       "final_matching_ids":[r["id"] for r in structured["records"] if case_matches(case,r)]})
    catalog_false=[r for r in readable if r["page"]==1 and r["category"]!="项目"]
    wrong_catalog_institute=[r for r in readable if r["page"]==1 and r["category"]=="项目" and r["fields"].get("设计院名称")]
    interface_port_errors=[]
    for row in readable:
        if row["page"]==5:
            for field in ("A端端口","Z端端口"):
                if matches(r"L[-－](?:16|64)[.．]2",row["fields"].get(field)):
                    interface_port_errors.append({"id":row["id"],"field":field,"value":row["fields"][field]})
    result={"scope":protocol["scope"],"check_count":len(checks),
            "raw_complete_matches":sum(bool(c["raw_matching_ids"]) for c in checks),
            "final_complete_matches":sum(bool(c["final_matching_ids"]) for c in checks),
            "not_overall_accuracy":True,
            "audit_only_recovered_records":sum(not r["included_in_strict_pipeline"] for r in readable),
            "raw_stage":"after_evidence_gate" if run.get("automatic_crops") else "original_page_extraction",
            "strict_failed_pages":[p["page"] for p in pages if not p.get("page_complete",p["extraction"] is not None)],
            "retried_pages":[p["page"] for p in pages if (any((out/"calls"/f"{p['page']:03d}").rglob("*-1.json")) or any((out/"routing-calls"/f"{p['page']:03d}").rglob("*-1.json")) if run.get("automatic_crops") else len(p["attempts"])>1)],
            "token_limit_attempts":sum(a["hit_token_limit"] for p in pages for a in p["attempts"]),
            "confirmed_catalog_business_false_positives":[r["id"] for r in catalog_false],
            "catalog_institute_wrong_assignments":[r["id"] for r in wrong_catalog_institute],
            "interface_type_used_as_port_on_page_5":interface_port_errors,
            "checks":checks}
    result["distinct_record_checks"] = []
    for prefix, expected in [("optical_",3),("protection_",4)]:
        subset=[c for c in checks if c["id"].startswith(prefix)]
        for kind in ("raw","final"):
            graph=nx.Graph()
            left=[("check",c["id"]) for c in subset]
            graph.add_nodes_from(left,bipartite=0)
            for c in subset:
                graph.add_edges_from([(("check",c["id"]),("record",r)) for r in c[f"{kind}_matching_ids"]])
            matching=nx.algorithms.bipartite.maximum_matching(graph,top_nodes=left)
            count=sum(node in matching for node in left)
            result["distinct_record_checks"].append({"category":prefix,"stage":kind,
                                                       "expected_distinct_records":expected,
                                                       "matched_distinct_records":count})
    (out/"audit/results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="checks"},ensure_ascii=False,indent=2))
    for c in checks:
        print(c['id'], 'raw=',c['raw_matching_ids'],'final=',c['final_matching_ids'])
    return result


if __name__=="__main__":
    audit(sys.argv[1])
