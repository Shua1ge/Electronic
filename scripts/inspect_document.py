"""Extract local PDF/DXF evidence; coordinates are retained, not inferred by an LLM."""
import argparse
import json
import math
import os
from pathlib import Path

os.environ.setdefault("XDG_CACHE_HOME", str(Path(__file__).resolve().parents[1] / ".cache"))

import ezdxf
import pymupdf
import pytesseract
from PIL import Image


def inspect_pdf(path, dpi=300, selected_pages=None, ocr_mode="auto", max_pixels=12_000_000):
    pages = []
    with pymupdf.open(path) as doc:
        for number in (range(len(doc)) if selected_pages is None else selected_pages):
            if not 0 <= number < len(doc):
                raise ValueError(f"Page {number+1} outside PDF with {len(doc)} pages")
            page = doc[number]
            words = page.get_text("words")
            evidence = [{"text": w[4], "bbox": list(w[:4]), "source": "pdf_text"} for w in words]
            path_count = len(page.get_cdrawings())
            method = "pdf_text"
            # Heuristic: many paths can be outlined glyphs, even with a small text layer.
            # It is intentionally conservative; this is not a semantic page classifier.
            needs_ocr = ocr_mode == "always" or (ocr_mode == "auto" and (not words or path_count > 5000))
            effective_dpi = None
            reason = "forced" if ocr_mode == "always" else ("no_text_layer" if not words else "dense_vector_paths")
            if needs_ocr:
                effective_dpi = min(dpi, max(1, int(72 * math.sqrt(max_pixels / page.rect.get_area()))))
                pix = page.get_pixmap(dpi=effective_dpi, colorspace=pymupdf.csRGB, alpha=False)
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                result = pytesseract.image_to_data(
                    image, lang="chi_sim+eng", config="--psm 11",
                    output_type=pytesseract.Output.DICT)
                scale = 72 / effective_dpi
                for i, text in enumerate(result["text"]):
                    if text.strip():
                        x, y = result["left"][i], result["top"][i]
                        w, h = result["width"][i], result["height"][i]
                        evidence.append({"text": text, "bbox": [v * scale for v in (x, y, x+w, y+h)],
                                         "ocr_confidence": float(result["conf"][i]), "source": "tesseract"})
                method = "pdf_text+tesseract" if words else "tesseract"
            pages.append({"page": number + 1, "method": method,
                          "ocr_reason": reason if needs_ocr else None,
                          "ocr_dpi": effective_dpi,
                          "native_word_count": len(words),
                          "overlapping_native_and_ocr_words_possible": bool(words and needs_ocr),
                          "coordinate_system": "page_points_top_left",
                          "page_size": [page.rect.width, page.rect.height],
                          "words": evidence, "vector_path_count": path_count})
    return {"source": str(Path(path).resolve()), "pages": pages}


def inspect_dxf(path):
    doc = ezdxf.readfile(path)
    entities = []
    # Baseline modelspace extraction; block expansion and paper layouts are future work.
    for entity in doc.modelspace():
        row = {"id": entity.dxf.handle, "type": entity.dxftype(), "layer": entity.dxf.layer}
        if entity.dxftype() in ("TEXT", "MTEXT"):
            row.update(text=entity.dxf.text if entity.dxftype() == "TEXT" else entity.plain_text(),
                       insert=list(entity.dxf.insert))
        elif entity.dxftype() == "LINE":
            row.update(start=list(entity.dxf.start), end=list(entity.dxf.end))
        entities.append(row)
    return {"source": str(Path(path).resolve()), "units_code": doc.units, "entities": entities}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pages", type=int, nargs="+", help="1-based PDF page numbers; default all")
    parser.add_argument("--ocr", choices=["auto", "always", "never"], default="auto")
    args = parser.parse_args()
    suffix = args.input.suffix.lower()
    if suffix == ".pdf":
        result = inspect_pdf(args.input, selected_pages=[p-1 for p in args.pages] if args.pages else None,
                             ocr_mode=args.ocr)
    elif suffix == ".dxf":
        result = inspect_dxf(args.input)
    else:
        parser.error("Expected PDF or DXF. Convert DWG with ODA first.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
