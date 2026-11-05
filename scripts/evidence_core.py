"""Evidence checks and conservative entity aggregation; no document-specific answers."""
import json
import re
import unicodedata
from collections import Counter, defaultdict

from document_core import FIELDS, aggregate, missing_value, parse_json


def norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold().replace('×','x')


def literal_supported(value, text):
    """Literal corroboration, not a claim that OCR is correct or refers to this object."""
    v, t = norm(value), norm(text)
    if not v:
        return False
    # Equivalent unit spellings; never correct station names or OCR digits.
    for a, b in [("gbit/s", "gb/s"), ("gbps", "gb/s"), ("mbit/s", "mb/s"),
                 ("mbps", "mb/s"), ("千米", "km"), ("公里", "km")]:
        v, t = v.replace(a,b), t.replace(a,b)
    if re.fullmatch(r"\d+(?:\.\d+)?(?:x\d+)?(?:芯|[a-z]+(?:/[a-z]+)?)?", v):
        return re.search(r"(?<![\d.])"+re.escape(v)+r"(?![\d.])", t) is not None
    return v in t


LITERAL_FIELDS = {"设计院名称", "项目名称", "A端站点", "Z端站点", "芯数", "光缆类型",
                  "光缆长度", "纤芯型号", "一次线路电压等级", "保护装置型号",
                  "申请光路速率", "申请业务速率", "A端端口", "Z端端口"}


def decode(raw):
    """Accept one JSON object with optional fence, never repair truncated values."""
    return parse_json(raw)


def explicit_field_normalizations(category, fields, source_text):
    """Move explicit tokens already in a business name into their typed columns.

    Do not infer a missing model from the station, channel number or outside data.
    Require one unique token and literal corroboration in the cited OCR text.
    """
    result={}
    name=str(fields.get('业务名称') or '')
    for key in ('A端站点','Z端站点'):
        value=str(fields.get(key) or '')
        match=re.fullmatch(r'(.+?)[（(]([^（）()]+)[）)]',value)
        if match and re.search(r'设备|传输|主干网|地区城网|烽火|华为|OSN|IAD|路由器',match[2],re.I):
            result[key]=match[1].strip()
    if category=='保护业务' and missing_value(fields.get('保护装置型号')):
        tokens=set(re.findall(r'(?<![A-Za-z0-9])[A-Z]{2,6}-\d[A-Za-z0-9]*(?:-[A-Za-z0-9]+)+',name))
        matches=[t for t in tokens if literal_supported(t,source_text)]
        if len(matches)==1:result['保护装置型号']=matches[0]
    if category=='非保护业务' and missing_value(fields.get('申请有效期类型')) and '临时' in name and '临时' in source_text:
        result['申请有效期类型']='临时'
    if category=='非保护业务' and missing_value(fields.get('申请业务速率')):
        labels=name+' '+str(fields.get('中途通道资源使用情况') or '')
        values=set(re.findall(r'(\d+(?:\.\d+)?(?:[×xX]\d+)?[MG])(?:业务|数字)?通道',labels))
        matching=[v for v in values if literal_supported(v,source_text)]
        if len(matching)==1:result['申请业务速率']=matching[0]
    return result


def validate_records(raw, category, lines):
    obj = decode(raw)
    if not isinstance(obj,dict) or not isinstance(obj.get("records"),list):
        raise ValueError("Expected object with records array")
    indexed={r["id"]:r for r in lines}
    records, rejected = [], []
    for number, candidate in enumerate(obj["records"],1):
        if not isinstance(candidate,dict) or not isinstance(candidate.get("fields"),dict):
            rejected.append({"record":number,"reason":"invalid record structure","raw":candidate})
            continue
        candidate={**candidate,'fields':dict(candidate['fields'])}
        original_fields=dict(candidate['fields'])
        source_ids=candidate.get('source_ids',[])
        source_text='\n'.join(indexed[i]['text'] for i in source_ids if isinstance(i,str) and i in indexed) if isinstance(source_ids,list) else ''
        protection_anchor=bool(re.search(r'(?:线路|光纤)保护|保护(?:装置|通道)|[套路]保护',source_text))
        if category=='保护业务' and not protection_anchor:
            rejected.append({'record':number,'reason':'no cited line-protection evidence for protection category','raw':candidate})
            continue
        label=str(candidate['fields'].get('业务名称') or '')+' '+str(candidate['fields'].get('业务类型') or '')
        if category=='非保护业务' and protection_anchor and re.search(r'线路保护|套保护|保护通道',label):
            rejected.append({'record':number,'reason':'explicit line-protection label conflicts with non-protection category','raw':candidate})
            continue
        normalizations=explicit_field_normalizations(category,candidate['fields'],source_text)
        candidate['fields'].update(normalizations)
        fields, evidence, checks, withheld = {}, [], {}, {}
        citations=candidate.get("citations",{})
        if not isinstance(citations,dict): citations={}
        common_refs=candidate.get("source_ids",[])
        if isinstance(common_refs,list):
            common_refs=[i for i in common_refs if isinstance(i,str) and i in indexed]
            for key,value in candidate["fields"].items():
                if key not in citations:
                    matching=[i for i in common_refs if literal_supported(value,indexed[i]["text"])]
                    citations[key]=matching if key in LITERAL_FIELDS else common_refs
        for key, value in candidate["fields"].items():
            if missing_value(value): continue
            if key not in FIELDS[category] or not isinstance(value,(str,int,float)) or isinstance(value,bool):
                withheld[key]={"value":value,"reason":"unknown field or non-scalar value"}
                continue
            refs=citations.get(key,[])
            if isinstance(refs,str): refs=[refs]
            refs=[i for i in refs if isinstance(i,str) and i in indexed] if isinstance(refs,list) else []
            quoted="\n".join(indexed[i]["text"] for i in refs)
            supported=literal_supported(value,quoted)
            supplementary=False
            if key in LITERAL_FIELDS and not supported:
                # Resolve an omitted/wrong citation only when the exact value is
                # unique in the same task's source context. This corroborates text,
                # not the model's claim about which wire/endpoint it belongs to.
                for form in dict.fromkeys((original_fields.get(key,value),value)):
                    hits=[r for r in lines if literal_supported(form,r['text'])]
                    if len(hits)==1:
                        refs=[hits[0]['id']];quoted=hits[0]['text'];supported=literal_supported(value,quoted)
                        supplementary=supported
                        if supported:break
            # Descriptive/relational fields need traceable source lines; their semantics
            # remain unverified. Exact-value fields additionally need literal corroboration.
            if not refs or (key in LITERAL_FIELDS and not supported):
                withheld[key]={"value":value,"citations":refs,"reason":"no literal OCR corroboration" if refs else "missing/invalid source IDs"}
                continue
            if key in {"A端端口","Z端端口"} and re.match(r"^(?:[LSI][-－]\d+[.．]\d|(?:NSR|PSL|RCS|PCS)-)",str(value),re.I):
                withheld[key]={"value":value,"citations":refs,"reason":"interface/device model is not a port identifier"}
                continue
            fields[key]=value
            checks[key]={"source_ids":refs,"check":"unique_context_literal_match_relationship_unverified" if supplementary else ("literal_ocr_match" if supported else "cited_relationship_unverified"),
                         "sources":[{"id":i,"text":indexed[i]["text"],"bbox":indexed[i]["bbox"]} for i in refs]}
            evidence.extend(refs)
        uncertainties=candidate.get("uncertainties",[])
        if not isinstance(uncertainties,list): uncertainties=[uncertainties]
        uncertainties=[x if isinstance(x,str) else json.dumps(x,ensure_ascii=False) for x in uncertainties]
        if withheld: uncertainties.append("未核实字段未进入业务表："+json.dumps(withheld,ensure_ascii=False))
        if not fields:
            rejected.append({"record":number,"reason":"no supported fields","raw":candidate,"withheld":withheld})
            continue
        refs=list(dict.fromkeys(evidence))
        records.append({"category":category,"fields":fields,
                        "evidence":"; ".join(f"{i}: {indexed[i]['text']}" for i in refs),
                        "uncertainties":uncertainties,"field_evidence":checks,
                        "withheld_fields":withheld,"model_record_number":number})
        records[-1]['field_normalizations']=normalizations
    return {"records":records,"rejected_records":rejected,"unresolved":obj.get("unresolved",[])}


def identity(row):
    """Only project metadata is automatically deduplicated in this release.

    Business identifiers in these drawings are not globally unique. Even identical
    endpoints, rates and equipment may refer to independent redundant resources.
    Preserve each observation until a topology-aware identity resolver exists.
    """
    f=row["fields"]
    if row["category"]=="项目" and all(not missing_value(f.get(k)) for k in ("项目名称","设计院名称")):
        return tuple(norm(f[k]) for k in ("项目名称","设计院名称"))
    return None


def safe_aggregate(observations):
    buckets=defaultdict(list)
    for r in observations:
        key=identity(r)
        if key: buckets[key].append(r)
    groups=[]
    for rows in buckets.values():
        # Existing aggregator normalizes whitespace but not arbitrary text changes.
        if len(rows)>1 and all(rows[0]["fields"]==r["fields"] for r in rows[1:]):
            groups.append({"source_ids":[r["id"] for r in rows],"reason":"设计院及项目名称逐字相同；仅去重项目元数据"})
    result=aggregate(observations,{"groups":groups,"links":[]})
    result["normalization"]="exact_project_dedup_only; all business observations preserved separately"
    # Include rejected field evidence in the final record's review trail as well.
    indexed={r["id"]:r for r in observations}
    for row in result["records"]:
        row["field_evidence"]={i:indexed[i].get("field_evidence",{}) for i in row["source_ids"]}
        row["withheld_fields"]={i:indexed[i].get("withheld_fields",{}) for i in row["source_ids"] if indexed[i].get("withheld_fields")}
        if row["withheld_fields"]: row["status"]="存在未核实字段，待复核"
    assert Counter(i for r in result["records"] for i in r["source_ids"])==Counter(r["id"] for r in observations)
    return result
