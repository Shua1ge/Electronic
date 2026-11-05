"""Offline tests for the v0.3 station-to-circle candidate linker (no GPU required)."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("station_node_linker", ROOT / "scripts/station_node_linker.py")
linker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linker)


class StationNodeTests(unittest.TestCase):
    def test_parse_fenced_json(self):
        fence = chr(96) * 3
        raw = fence + 'json\n{"站名":"李家垸","其他可能读法":["李家垠"],"看不清":false}\n' + fence
        primary, alternatives, error = linker.clean_reply(raw)
        self.assertIsNone(error)
        self.assertEqual(primary, "李家垸")
        self.assertEqual(alternatives, ["李家垠"])

    def test_reject_cable_annotation_as_station_name(self):
        self.assertIsNone(linker.clean_name("24芯/11.3km OPGW"))
        self.assertEqual(linker.clean_name("500kV十堰变"), "500kV十堰变")

    def test_conflicting_ocr_and_qwen_are_not_auto_bound(self):
        node = linker.node_candidate(
            {"id": "N005"}, [{"name": "李家垠", "source": "crop_tesseract"}],
            {"name": "李家垸", "alternatives": []},
        )
        self.assertIsNone(node["primary_name"])
        self.assertIn("不同读法", node["state"])

    def test_fuzzy_station_name_not_silently_fixed(self):
        nodes = [
            {"node_id": "N005", "primary_name": "李家垸", "name_options": [{"name": "李家垸", "source": "Qwen"}]},
            {"node_id": "N001", "primary_name": "乾兴", "name_options": [{"name": "乾兴", "source": "Qwen"}]},
        ]
        report = linker.link_record(
            {"start": "李家垠", "end": "乾兴"}, nodes,
            [{"id": "E001", "a_node": "N005", "b_node": "N001"}],
        )
        self.assertEqual(report["node_ids"], [None, "N001"])
        self.assertIsNone(report["edge_id"])
        self.assertTrue(report["suggestions"]["起点"])

    def test_exact_unique_match_is_provisional_only(self):
        nodes = [
            {"node_id": "N001", "primary_name": "柏林", "name_options": [{"name": "柏林", "source": "OCR"}]},
            {"node_id": "N002", "primary_name": "炉子沟", "name_options": [{"name": "炉子沟", "source": "Qwen"}]},
        ]
        report = linker.link_record(
            {"start": "柏林", "end": "炉子沟"}, nodes,
            [{"id": "E007", "a_node": "N001", "b_node": "N002"}],
        )
        self.assertEqual(report["edge_id"], "E007")
        self.assertEqual(report["approval"], "未人工确认")

    def test_duplicate_station_name_is_ambiguous(self):
        nodes = [
            {"node_id": "N001", "primary_name": "柏林", "name_options": [{"name": "柏林", "source": "OCR"}]},
            {"node_id": "N002", "primary_name": "柏林", "name_options": [{"name": "柏林", "source": "Qwen"}]},
        ]
        report = linker.link_record({"start": "柏林", "end": "柏林"}, nodes, [])
        self.assertEqual(report["node_ids"], [None, None])

    def test_geometry_page_shapes(self):
        page = {"page": 1, "nodes": []}
        self.assertEqual(linker.geometry_pages({"pages": [page]}), [page])
        self.assertEqual(linker.geometry_pages({"pages": {"1": page}}), [page])

    def test_replay_writes_separate_reports(self):
        import pymupdf
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "drawing.pdf"
            doc = pymupdf.open()
            doc.new_page(width=500, height=300)
            doc.save(pdf)
            doc.close()
            out = root / "output"
            (out / "geometry").mkdir(parents=True)
            geom = {"pages": [{"page": 1, "width_pt": 500, "height_pt": 300,
                 "nodes": [{"id": "N001", "x_pt": 90, "y_pt": 90, "radius_pt": 6},
                           {"id": "N002", "x_pt": 360, "y_pt": 90, "radius_pt": 6}],
                 "edges": [{"id": "E001", "a_node": "N001", "b_node": "N002"}]}]}
            (out / "geometry/geometry.json").write_text(json.dumps(geom), encoding="utf-8")
            (out / "candidates.json").write_text(json.dumps(
                {"records": [{"id": "C0001", "page": 1, "start": "柏林", "end": "炉子沟", "status": "待核对"}]}),
                encoding="utf-8")
            (out / "ocr.json").write_text('{"pages":{"1":[]}}', encoding="utf-8")
            (out / "review.html").write_text("<html><body>原始复核</body></html>", encoding="utf-8")
            wb = Workbook()
            wb.active.title = "光缆候选"
            wb.save(out / "structured.xlsx")
            replies = root / "replies"
            replies.mkdir()
            (replies / "page001_N001.txt").write_text(
                '{"站名":"柏林","其他可能读法":[],"看不清":false}', encoding="utf-8")
            (replies / "page001_N002.txt").write_text(
                '{"站名":"炉子沟","其他可能读法":[],"看不清":false}', encoding="utf-8")
            result = linker.run(pdf, out, mode="crops", replies=replies)
            self.assertEqual(result["with_direct_edge"], 1)
            self.assertTrue((out / "review-with-nodes.html").exists())
            self.assertTrue((out / "structured-with-nodes.xlsx").exists())
            self.assertTrue((out / "geometry/station_name_candidates.json").exists())
            self.assertIn("原始复核", (out / "review.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
