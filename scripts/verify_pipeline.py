"""Deterministic environment smoke tests, not a drawing accuracy benchmark."""
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))

import cv2
import ezdxf
import networkx as nx
import pymupdf
import pytesseract
from openpyxl import Workbook, load_workbook
from shapely.geometry import LineString

from inspect_document import inspect_pdf, inspect_dxf

OUT = ROOT / "outputs/verification"
OUT.mkdir(parents=True, exist_ok=True)
report = {"python": sys.version, "platform": platform.platform(), "checks": {}}
report["packages"] = {name: importlib.metadata.version(name) for name in
                      ["mlx", "mlx-vlm", "transformers", "pymupdf", "ezdxf", "opencv-python",
                       "pillow", "pytesseract", "networkx", "shapely", "pydantic", "openpyxl", "huggingface-hub"]}
assert sys.version_info[:2] == (3, 13)
assert "chi_sim" in pytesseract.get_languages()
report["checks"]["chinese_ocr_language"] = True

doc = pymupdf.open()
page = doc.new_page(width=800, height=400)
page.insert_text((40, 60), "SITE A - SITE B   OPGW 72 cores 72 km", fontsize=22)
page.insert_text((40, 120), "通信图纸测试 项目A 丁站", fontsize=24, fontname="china-s")
page.draw_line((100, 200), (650, 200))
doc.save(OUT / "vector.pdf")
pix = page.get_pixmap(dpi=200)
scan = pymupdf.open()
sp = scan.new_page(width=800, height=400)
sp.insert_image(sp.rect, stream=pix.tobytes("png"))
scan.save(OUT / "scan.pdf")
doc.close()
scan.close()
for filename, method in [("vector.pdf", "pdf_text"), ("scan.pdf", "tesseract")]:
    result = inspect_pdf(OUT / filename)
    p = result["pages"][0]
    text = " ".join(w["text"] for w in p["words"])
    assert p["method"] == method, p
    assert "OPGW" in text and "72" in text, text
    assert all(len(w["bbox"]) == 4 for w in p["words"])
    if method == "pdf_text":
        assert p["vector_path_count"] > 0
    (OUT / f"{filename}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    report["checks"][method + "_extraction"] = {"passed": True, "text": text}

dxf = ezdxf.new("R2018")
ms = dxf.modelspace()
ms.add_text("OPGW 72", dxfattribs={"insert": (10, 20), "height": 2})
ms.add_line((0, 0), (100, 0))
dxf.saveas(OUT / "sample.dxf")
result = inspect_dxf(OUT / "sample.dxf")
assert any(e.get("text") == "OPGW 72" for e in result["entities"])
assert any(e["type"] == "LINE" for e in result["entities"])
(OUT / "sample.dxf.json").write_text(json.dumps(result, indent=2))
report["checks"]["dxf_entities"] = True
assert LineString([(0, 0), (1, 1)]).intersects(LineString([(0, 1), (1, 0)]))
graph = nx.Graph([(1, 2), (2, 3)])
assert nx.shortest_path(graph, 1, 3) == [1, 2, 3]
import numpy as np
assert cv2.Canny(np.zeros((20, 20), dtype=np.uint8), 50, 100).shape == (20, 20)
book = Workbook()
book.active.append(["cable_type", "cores"])
book.active.append(["OPGW", 72])
book.save(OUT / "sample.xlsx")
assert load_workbook(OUT / "sample.xlsx").active["B2"].value == 72
report["checks"]["geometry_graph_opencv_xlsx"] = True
for binary in ["pdftotext", "pdftoppm", "tesseract"]:
    assert shutil.which(binary), binary
    report["checks"][binary] = shutil.which(binary)
report["brew"] = subprocess.check_output(["/opt/homebrew/bin/brew", "list", "--versions",
                                         "python@3.13", "uv", "poppler", "tesseract", "tesseract-lang"], text=True)
(OUT / "environment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
