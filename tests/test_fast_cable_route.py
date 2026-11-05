"""Pure, GPU-free regression tests for the Ubuntu Fast cable prototype."""
from __future__ import annotations
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "scripts" / "fast_cable_route.py"
sys.path.insert(0, str(MOD.parent))
spec = importlib.util.spec_from_file_location("fast_cable_route", MOD)
m = importlib.util.module_from_spec(spec)
sys.modules["fast_cable_route"] = m
spec.loader.exec_module(m)


class ParserTests(unittest.TestCase):
    def test_markdown_json_reply(self):
        d, err = m.parse_reply('```json\n{"站点":[],"光缆连接":[],"待核对项":[]}\n```')
        self.assertIsNone(err)
        self.assertEqual(d["光缆连接"], [])

    def test_partial_json_is_not_silently_repaired(self):
        d, err = m.parse_reply('{"光缆连接":[{"起点":"A"}')
        self.assertIsNone(d)
        self.assertIn("无法解析", err)

    def test_array_masquerading_as_object_rejected(self):
        d, err = m.parse_reply('[{"光缆连接":[]}]')
        self.assertIsNone(d)
        self.assertIn("JSON 对象", err)

    def test_units(self):
        self.assertEqual(m.parse_length("25km"), (25.0, None))
        self.assertEqual(m.parse_length("11.3km"), (11.3, None))
        self.assertIsNotNone(m.parse_length("11.4.2km")[1])
        self.assertEqual(m.parse_cores("24芯"), (24, None))
        self.assertIsNotNone(m.parse_cores("很多芯")[1])

    def test_geometry_never_auto_approves(self):
        ocr = [{"id": "T1", "text": "柏林", "bbox": [0, 0, .2, .2]},
               {"id": "T2", "text": "炉子沟", "bbox": [.3, 0, .4, .2]},
               {"id": "T3", "text": "24芯/11.3km OPGW", "bbox": [0, .3, .6, .4]}]
        r = m.candidate_from_model({"起点": "柏林", "终点": "炉子沟", "芯数": "24芯",
                                    "长度km": "11.3km", "类型": "OPGW"}, 1, "page001_tile01", ocr, 1)
        self.assertEqual(r["status"], "待核对")
        self.assertIn("T1", r["ocr_ids"]["起点"])
        self.assertIn("T3", r["ocr_ids"]["长度"])
        self.assertIn("拓扑未核实", r["evidence_check"])

    def test_self_loop_is_rejected(self):
        r = m.candidate_from_model({"起点": "十堰", "终点": "十堰", "长度km": "17.42km"}, 1, "t", [], 1)
        self.assertEqual(r["status"], "拒绝")
        self.assertIn("自环", r["blockers"][0])

    def test_model_station_spelling_not_auto_corrected(self):
        r = m.candidate_from_model({"起点": "李家垠", "终点": "乾兴", "长度km": "55km"}, 1, "t", [], 1)
        self.assertEqual(r["start"], "李家垠")
        self.assertEqual(r["status"], "待核对")
        self.assertTrue(any("OCR未独立核实起点" in s for s in r["notes"]))

    def test_duplicate_identical_only_parallel_preserved(self):
        first = m.candidate_from_model({"起点": "甲", "终点": "乙", "长度km": "10km"}, 1, "t1", [], 1)
        same = m.candidate_from_model({"起点": "乙", "终点": "甲", "长度km": "10km"}, 1, "t2", [], 2)
        parallel = m.candidate_from_model({"起点": "甲", "终点": "乙", "长度km": "12km"}, 1, "t3", [], 3)
        rs = m.deduplicate([first, same, parallel])
        self.assertEqual(len(rs), 2)
        self.assertEqual(rs[0]["other_tiles"], ["t2"])
        m.crosscheck_parallel(rs)
        self.assertIn("并行", "".join(rs[0]["notes"]))

    def test_tiles_cover_corners(self):
        boxes = m.grid_boxes(3, 2, .12)
        self.assertEqual(len(boxes), 6)
        self.assertEqual(boxes[0][:2], (0., 0.))
        self.assertEqual(boxes[-1][2:], (1., 1.))

    def test_formula_payload_is_escaped(self):
        self.assertEqual(m._xlsx_safe("=HYPERLINK(\"bad\")"), "'=HYPERLINK(\"bad\")")


class IntegrationTests(unittest.TestCase):
    def test_offline_replay_creates_review_outputs(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            pdf = root / "tiny.pdf"
            doc = pymupdf.open()
            page = doc.new_page(width=500, height=300)
            page.insert_text((40, 80), "Station A")
            page.insert_text((240, 80), "Station B")
            doc.save(pdf)
            doc.close()
            reply_dir = root / "replies"
            reply_dir.mkdir()
            (reply_dir / "page001_tile01.txt").write_text(json.dumps({
                "站点": ["Station A", "Station B"],
                "光缆连接": [{"起点": "Station A", "终点": "Station B", "类型": "OPGW",
                                  "芯数": "24芯", "长度km": "11.3km", "建设状态": None}],
                "待核对项": []}, ensure_ascii=False), encoding="utf-8")
            output = root / "result"
            args = m.make_parser().parse_args([str(pdf), "--output", str(output),
                                                "--responses-dir", str(reply_dir),
                                                "--cols", "1", "--rows", "1", "--ocr", "none",
                                                "--render-long-edge", "1400"])
            result = m.run(args)
            self.assertEqual(len(result["records"]), 1)
            self.assertEqual(result["records"][0]["status"], "待核对")
            for filename in ("geometry/geometry.json", "geometry/station_anchors.csv", "structured.xlsx", "review.html", "review.csv", "candidates.json", "ocr.json",
                             "tiles.json", "manifest.json", "calls/page001_tile01.json"):
                self.assertTrue((output / filename).exists(), filename)
            with self.assertRaises(FileExistsError):
                m.run(args)


class GeometryTests(unittest.TestCase):
    @staticmethod
    def nodes():
        return [
            {"id":"N001", "x_pt":15, "y_pt":20, "radius_pt":5, "confirmed_name":"甲站"},
            {"id":"N002", "x_pt":115,"y_pt":20, "radius_pt":5, "confirmed_name":"乙站"},
        ]

    def test_direct_stroke_joins_circles_not_crossing(self):
        from cable_route_geometry import direct_edges
        n = self.nodes()
        s=[{"a":[20,20],"b":[110,20],"length_pt":90,"type":"s"}]
        edges=direct_edges(n,s)
        self.assertEqual(len(edges),1)
        self.assertEqual((edges[0]["a_node"],edges[0]["b_node"]),("N001","N002"))
        # A crossing in the middle is not turned into a third endpoint.
        self.assertEqual(len(edges),1)

    def test_third_station_on_segment_blocks_bypass(self):
        from cable_route_geometry import direct_edges
        n=self.nodes()+[{"id":"N003","x_pt":65,"y_pt":20,"radius_pt":5}]
        s=[{"a":[20,20],"b":[110,20],"length_pt":90,"type":"s"}]
        self.assertEqual(direct_edges(n,s),[])

    def test_manual_station_labels_allow_only_geometry_hint_not_approval(self):
        from cable_route_geometry import check_candidate
        n=self.nodes()
        graph={"pages":[{"page":1,"nodes":n,"edges":[{"id":"E001","a_node":"N001","b_node":"N002"}]}]}
        row={"page":1,"start":"甲站","end":"乙站","status":"待核对"}
        verdict=check_candidate(row,graph)
        self.assertIn("直连矢量线段",verdict["state"])
        self.assertEqual(verdict["edge_id"],"E001")
        self.assertEqual(row["status"],"待核对")
        row["end"]="丙站"
        self.assertIn("尚未标注",check_candidate(row,graph)["state"])
        graph["pages"][0]["edges"]=[]
        row["end"]="乙站"
        self.assertIn("未检出",check_candidate(row,graph)["state"])

    def test_operator_map_duplicate_and_unknown_rejected(self):
        from cable_route_geometry import read_station_map
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"anchors.csv"
            path.write_text("页码,节点ID,确认站名\n1,N001,甲站\n1,N002,甲站\n",encoding="utf-8")
            pages=[{"page":1,"nodes":self.nodes()}]
            with self.assertRaises(ValueError):
                read_station_map(path,pages)
            path.write_text("页码,节点ID,确认站名\n1,N999,甲站\n",encoding="utf-8")
            with self.assertRaises(ValueError):
                read_station_map(path,pages)

    def test_geometry_only_creates_graph_without_cuda(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            pdf=root/"diagram.pdf"
            doc=pymupdf.open()
            page=doc.new_page(width=400,height=250)
            for x in (80,290):
                page.draw_circle((x,80),7)
                page.draw_circle((x,80),5)
            page.draw_line((87,80),(283,80))
            doc.save(pdf)
            doc.close()
            output=root/"out"
            args=m.make_parser().parse_args([str(pdf),"--output",str(output),"--ocr","none",
                                                "--geometry-only","--render-long-edge","1400"])
            report=m.run(args)
            self.assertEqual(report["records"],[])
            self.assertTrue((output/"geometry/page001_overlay.png").exists())
            self.assertTrue((output/"geometry/station_anchors.csv").exists())
            self.assertEqual(report["manifest"]["generation_mode"],"geometry-only")


if __name__ == "__main__":
    unittest.main()