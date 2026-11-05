"""Reapply deterministic aggregation to saved observations; does not call a model."""
import json
import sys
from collections import Counter
from pathlib import Path

from document_core import aggregate
from export_document import export_run

out=Path(sys.argv[1])
observations=json.loads((out/"observations.json").read_text())
association=json.loads((out/"association.json").read_text())
combined=aggregate(observations,association["proposal"])
old=out/"structured-before-normalization.json"
if not old.exists():
    old.write_bytes((out/"structured.json").read_bytes())
(out/"structured.json").write_text(json.dumps(combined,ensure_ascii=False,indent=2)+"\n")
manifest=json.loads((out/"run.json").read_text())
manifest.update(output_records=len(combined["records"]),
                category_counts=dict(Counter(r["category"] for r in combined["records"])),
                conflicted_records=sum(bool(r["conflicts"]) for r in combined["records"]),
                candidate_links=len(combined["links"]),
                rejected_association_proposals=len(combined["rejected_proposals"]),
                deterministic_normalization=combined["normalization"])
(out/"run.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
export_run(out)
print(json.dumps(manifest,ensure_ascii=False,indent=2))
