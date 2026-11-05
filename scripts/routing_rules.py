"""Use a title actually read in the title block to constrain task selection."""
import re
from evidence_core import norm


def resolve_route(route, lines):
    if not route:return route
    title=norm(route.get("title") or "")
    # Never promote a guessed title or a phrase from the diagram body into a rule.
    sources=[l for l in lines if l["bbox"][1]>.75 and title and title in norm(l["text"])]
    patterns=[(r"施工图说明|设计说明","设计说明"),
              (r"业务|保护通道|通道组织|通道分配","业务组织"),
              (r"时钟|同步|网管","同步网管"),
              (r"板卡|槽位|板位|单板","设备板卡"),
              (r"站内.*(?:光缆|电缆|缆线).*(?:走向|敷设|布置)","站内布线"),
              (r"缆线.*(?:连接|联系)表|缆线表","站内布线"),
              (r"(?:电源|供电).*(?:连接|接线|系统).*图","安装布置"),
              (r"光通信系统配置|光纤通信系统配置","光通信系统"),
              (r"光缆.*(?:路由|径路|拓扑)","光缆路由")]
    candidates=[(title,sources)] if sources else []
    # Recover an OCR title when the model guessed a different title. Require the
    # lower-right title-block region and an explicit drawing/table title ending.
    candidates += [(norm(l['text']),[l]) for l in lines
                   if l['bbox'][1]>.75 and l['bbox'][0]>.45 and re.search(r'(?:图|表|说明)$',norm(l['text']))]
    for candidate, evidence in candidates:
        for pattern,kind in patterns:
            if re.search(pattern,candidate):
                if route["kind"]==kind:return route
                return {**route,"kind":kind,"title":evidence[0]['text'],"model_kind":route["kind"],
                        "routing_override":{"reason":"verified title-block keyword", "source_ids":[l["id"] for l in evidence],"rule":pattern}}
    return route
