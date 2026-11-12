"""External JSON preannotation conversion safety checks; no GPU/network required."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("import_preannotations", ROOT / "scripts/import_preannotations.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def project():
    return {"revision": 0, "document": {"id": "DTEST", "sha256": "a"*64, "pages": [{"page": 1}]},
            "regions": [{"id": "R-manual", "type": "diagram", "status": "reviewed",
                         "data": {"objects": [], "connections": []}}], "links": []}


def payload():
    return {"schema_version": "electronic-preannotation-v1", "model": "external-vision-model",
            "document_id": "DTEST", "source_pdf_sha256": "a"*64,
            "regions": [{"page": 1, "bbox": [.1, .1, .8, .8], "bbox_space": "region", "type": "diagram",
                         "title": "局部线路", "data": {"region_role": "main_diagram",
                         "objects": [{"id": "a", "bbox": [.1, .1, .2, .2], "kind": "station", "name": "A站"},
                                     {"id": "b", "bbox": [.7, .7, .8, .8], "kind": "station", "name": "B站"}],
                         "connections": [{"id": "e1", "source": "a", "target": "b", "relation": "cable",
                                          "directed": False, "attributes": {
                                              "cable_type": "OPGW", "cores": {"value": 24, "raw": "24芯"},
                                              "length": {"value": 11.3, "unit": "km", "raw": "11.3km"}}}]}}]}


class ImportConversionTest(unittest.TestCase):
    def test_convert_with_pending_not_gold(self):
        src = payload()
        regions, stats = mod.converted_regions(src, project(), "a"*64)
        self.assertEqual(stats["objects"], 2)
        self.assertEqual(stats["connections"], 1)
        r = regions[0]
        self.assertEqual(r["status"], "draft")
        self.assertTrue(r["title"].startswith("[AI草稿]"))
        self.assertEqual(r["data"]["objects"][0]["name_state"], "pending")
        edge = r["data"]["connections"][0]
        self.assertIn(edge["source"], {x["id"] for x in r["data"]["objects"]})
        self.assertNotEqual(edge["source"], edge["target"])
        self.assertEqual(edge["attributes"]["length"]["value"], 11.3)
        self.assertEqual(edge["attributes"]["length"]["unit"], "km")
        self.assertEqual(edge["attributes"]["length"]["state"], "pending")
        self.assertEqual(edge["attributes"]["length"]["evidence"], [])
        self.assertEqual(edge["path"], [])
        self.assertEqual(r["data"]["objects"][0]["bbox"], [.17, .17, .24, .24])
        self.assertEqual(len(project()["regions"]), 1)

    def test_bbox_space_mandatory(self):
        p = payload()
        del p["regions"][0]["bbox_space"]
        with self.assertRaisesRegex(ValueError, "bbox_space"):
            mod.converted_regions(p, project(), "a"*64)

    def test_source_hash_mismatch(self):
        with self.assertRaisesRegex(ValueError, "SHA256"):
            mod.converted_regions(payload(), project(), "b"*64)

    def test_document_id_mismatch(self):
        p = payload()
        p["document_id"] = "WRONG"
        with self.assertRaisesRegex(ValueError, "document_id"):
            mod.converted_regions(p, project(), "a"*64)

    def test_dangling_model_edge_rejected(self):
        p = payload()
        p["regions"][0]["data"]["connections"][0]["target"] = "missing"
        with self.assertRaisesRegex(ValueError, "起终点"):
            mod.converted_regions(p, project(), "a"*64)

    def test_duplicate_objects_rejected(self):
        p = payload()
        p["regions"][0]["data"]["objects"][1]["id"] = "a"
        with self.assertRaisesRegex(ValueError, "重复对象"):
            mod.converted_regions(p, project(), "a"*64)

    def test_bbox_outside_region_rejected(self):
        p = payload()
        p["regions"][0]["bbox_space"] = "page"
        p["regions"][0]["data"]["objects"][0]["bbox"] = [.02, .02, .07, .07]
        with self.assertRaisesRegex(ValueError, "超出了区域"):
            mod.converted_regions(p, project(), "a"*64)

    def test_model_path_is_not_human_path(self):
        p = payload()
        p["regions"][0]["data"]["connections"][0]["path"] = [[.1, .1], [.8, .8]]
        self.assertEqual(mod.converted_regions(p, project(), "a"*64)[0][0]["data"]["connections"][0]["path"], [])

    def test_text_table_imported_as_pending(self):
        p = payload()
        p["regions"] = [
            {"page": 1, "bbox": [.0, .0, .3, .3], "type": "text", "data": {"transcript": "说明书原文"}},
            {"page": 1, "bbox": [.3, .3, .9, .9], "type": "table", "data": {
                "rows": 1, "cols": 2, "header_rows": 0,
                "cells": [{"row": 0, "col": 0, "text": "名称"}, {"row": 0, "col": 1, "text": "型号"}]}}
        ]
        regions, stats = mod.converted_regions(p, project(), "a"*64)
        self.assertEqual(stats["text_regions"], 1)
        self.assertEqual(stats["table_regions"], 1)
        self.assertEqual(regions[0]["data"]["state"], "pending")
        self.assertTrue(all(c["state"] == "pending" for c in regions[1]["data"]["cells"]))

    def test_table_missing_cell_rejected(self):
        p = payload()
        p["regions"][0] = {"page": 1, "bbox": [0, 0, 1, 1], "type": "table", "data": {
            "rows": 1, "cols": 2, "cells": [{"row": 0, "col": 0, "text": "A"}]}}
        with self.assertRaisesRegex(ValueError, "未完全覆盖"):
            mod.converted_regions(p, project(), "a"*64)

    def test_external_evidence_not_promoted(self):
        p = payload()
        p["regions"][0]["data"]["objects"][0]["evidence"] = [{"image_id": "fake", "bbox": [0, 0, 1, 1]}]
        r = mod.converted_regions(p, project(), "a"*64)[0][0]
        self.assertEqual(r["data"]["objects"][0]["evidence"], [])

    def test_import_file_dry_run_then_save_and_block_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            filename = root / "model.json"
            filename.write_text(json.dumps(payload(), ensure_ascii=False), encoding="utf-8")
            shared = project()
            saved_revisions = []
            class MockStore:
                def __init__(self, p): self.root = p
                def load(self): return copy.deepcopy(shared)
                def check_source(self, proj): pass
                def save(self, rev, regions, links, actor):
                    self_case.assertEqual(rev, shared["revision"])
                    shared["regions"] = regions
                    shared["revision"] += 1
                    saved_revisions.append(actor)
            self_case = self
            fake_annotation = types.SimpleNamespace(AnnotationStore=MockStore,
                validate_region=lambda r, pages, reviewed=False: None)
            fake_core = types.SimpleNamespace(digest=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest(),
                 read_json=lambda p: json.loads(Path(p).read_text()))
            with patch.dict("sys.modules", {"document_annotation": fake_annotation, "document_eval_core": fake_core}):
                dry = mod.import_file(root, filename, "tester", dry_run=True)
                self.assertTrue(dry["dry_run"])
                self.assertEqual(len(shared["regions"]), 1)
                saved = mod.import_file(root, filename, "tester")
                self.assertFalse(saved["dry_run"])
                self.assertEqual(len(shared["regions"]), 2)
                self.assertEqual(shared["regions"][0]["status"], "reviewed")
                self.assertEqual(saved_revisions, ["tester"])
                with self.assertRaisesRegex(ValueError, "已经导入"):
                    mod.import_file(root, filename, "tester")

if __name__ == "__main__":
    unittest.main()