"""Independent source-authored checks; never imported by the inference pipeline."""
import json
import re
import sys
from pathlib import Path
import networkx as nx
from audit_document import case_matches as source_case_matches
from full_pdf import save,sha256


def case_matches(case,row):
    # A route/list is not a single endpoint. Substring matching otherwise credits
    # "A～B" as either A or B, incorrectly rewarding a malformed station field.
    if case.get('endpoints'):
        for key in ['A端站点','Z端站点']:
            value=re.sub(r'[（(].*?[）)]','',str(row.get('fields',{}).get(key) or ''))
            if re.search(r'[~～→↔、,，;/]|(?:变|站)\s*[-—－至]',value):return False
    return source_case_matches(case,row)


def matching(cases,rows,endpoints_only=False):
    graph=nx.Graph();left=[('case',c['id']) for c in cases];graph.add_nodes_from(left,bipartite=0)
    for c in cases:
        criterion={k:v for k,v in c.items() if k not in ['context','fields','pair_fields','pair_patterns']} if endpoints_only else c
        graph.add_edges_from((('case',c['id']),('row',r['id'])) for r in rows if case_matches(criterion,r))
    matched=nx.algorithms.bipartite.maximum_matching(graph,top_nodes=left)
    return {c[1]:matched[c][1] for c in left if c in matched}


def audit(out):
    out=Path(out);protocol=json.loads((out/'audit/protocol.json').read_text())
    run=json.loads((out/'run.json').read_text());rows=json.loads((out/'observations.json').read_text())
    structured=json.loads((out/'structured.json').read_text())['records']
    assert sha256(Path(run['input']))==protocol['pdf_sha256']==run['signature']['pdf_sha256']
    results=[]
    for c in protocol['checks']:
        results.append({**c,'raw_matching_ids':[r['id'] for r in rows if case_matches(c,r)],
                        'final_matching_ids':[r['id'] for r in structured if case_matches(c,r)]})
    result={'scope':protocol['scope'],'check_count':len(results),'not_overall_accuracy':True,
            'raw_complete_matches':sum(bool(c['raw_matching_ids']) for c in results),
            'final_complete_matches':sum(bool(c['final_matching_ids']) for c in results),'checks':results}
    save(out/'audit/results.json',result)
    save(out/'audit/raw-readable.json',[{**r,'included_in_strict_pipeline':True} for r in rows])
    channels=protocol.get('channels',[]);pages={p for c in channels for p in c['source_pages']}
    business=[r for r in rows if r['page'] in pages and r['category'] in ['保护业务','非保护业务']]
    complete=matching(channels,business);endpoints=matching(channels,business,True)
    coverage={'scope':'源业务图全部可见业务连线；一对一匹配，重复记录不能重复得分。端点指标只检查类别和A/Z，不代表业务属性正确。',
              'expected_channels':len(channels),'output_candidates':len(business),
              'strict_matches':len(complete),'endpoint_pair_matches':len(endpoints),
              'strict_assignment':complete,'endpoint_assignment':endpoints,
              'unmatched_source_channels':[c for c in channels if c['id'] not in complete],
              'unmatched_output_ids':[r['id'] for r in business if r['id'] not in complete.values()],
              'source_channels':channels}
    save(out/'audit/channel-results.json',coverage)
    print(json.dumps({'document':out.name,'facts':f"{result['final_complete_matches']}/{len(results)}",
                      'channels':f"{len(complete)}/{len(channels)}",'endpoints':len(endpoints),'candidates':len(business)},ensure_ascii=False))
    return result,coverage


if __name__=='__main__':
    for directory in sys.argv[1:]:audit(directory)
