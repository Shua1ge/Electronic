"""Convert local DWG/DXF in a temporary directory without touching the input."""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ODA = ROOT / "tools/oda/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("input", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
if not args.input.is_file() or args.input.suffix.lower() not in (".dwg", ".dxf"):
    parser.error("Input must be an existing DWG or DXF")
if args.output.suffix.lower() not in (".dwg", ".dxf"):
    parser.error("Output must end in .dwg or .dxf")
if args.output.exists():
    parser.error("Output already exists; choose another path")
if not ODA.is_file():
    parser.error("ODA converter is missing; see README.md")
with tempfile.TemporaryDirectory(prefix="telecom-cad-") as temp:
    base = Path(temp)
    source, target = base / "input", base / "output"
    source.mkdir()
    target.mkdir()
    shutil.copy2(args.input, source / args.input.name)
    subprocess.run([str(ODA), str(source), str(target), "ACAD2018",
                    args.output.suffix[1:].upper(), "0", "1", "*" + args.input.suffix],
                   check=True, timeout=120)
    candidates = [p for p in target.iterdir() if p.suffix.lower() == args.output.suffix.lower()]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one converted file; found {len(candidates)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], args.output)
print(args.output.resolve())
