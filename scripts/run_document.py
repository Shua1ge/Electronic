"""One-command automatic local PDF recognition, including diagram grouping."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('pdf',type=Path);parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--review-limit',type=int,default=3,help='Bounded evidence rereads; 0 only creates the queue')
args=parser.parse_args()
if args.review_limit<0:parser.error('review-limit must be nonnegative')
if not args.pdf.is_file():parser.error('PDF does not exist')
output=args.output.resolve();base=output.with_name(output.name+'.base')
steps=[['evidence_pdf.py',str(args.pdf.resolve()),'--output',str(base),'--defer-business'],
       ['refine_routing.py',str(base),'--output',str(output),'--diagram-regions','--channel-geometry'],
       ['revalidate_evidence.py',str(output)],['project_context.py',str(output)],['review_evidence.py',str(output),'--limit',str(args.review_limit)],['export_document.py',str(output)]]
for step in steps:
    print(f"Running {step[0]}",flush=True)
    subprocess.run([sys.executable,str(ROOT/'scripts'/step[0]),*step[1:]],check=True,cwd=ROOT)
print(f"Review: {output/'review.html'}",flush=True)
