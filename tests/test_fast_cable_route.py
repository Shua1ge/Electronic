"""GPU-free tests for the Ubuntu Fast cable route candidate pipeline."""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fast_cable_route", ROOT / "scripts" / "fast_cable_route.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class FastCableTests(unittest.TestCase):
    def test_parse_fenced_json(self):
        value, error = m.parse_reply('\\`\\`\\`json\\n{"光缆连接":[],"待核对项":[]}\\n\\`\\`\\`'.replace('\\\\', ''))
        self.assertIsNone(error)
        self.assertEqual(value["光缆连接"], [])

    def test_invalid_json_not_repaired(self):
        value, error = m.parse_reply('{"光缆连接":[{"起点":"A"}')
        self.assertIsNone(value)
        self.assertIsNotNone(error)

    def test_self_loop_rejected(self):
        item = m.candidate_from_model({"起点":"十堰","终点":"十堰","长度km":"17.42km"}, 1, "tile", [], 1)
        self.assertEqual(item["status"], "拒绝")

    def test_normal_link_not_auto_confirmed(self):
        ocr = [{"id":"T1","text":"柏林"},{"id":"T2","text":"炉子沟"},{"id":"T3","text":"24芯 11.3km OPGW"}]
        item = m.candidate_from_model({"起点":"柏林","终点":"炉子沟","芯数":"24芯","长度km":"11.3km"}, 1, "tile", ocr, 1)
        self.assertEqual(item["status"], "待核对")
        self.assertIn("拓扑未核实", item["evidence_check"])

    def test_keep_different_parallel_candidates(self):
        a = m.candidate_from_model({"起点":"甲","终点":"乙","长度km":"10km"}, 1, "a", [], 1)
        b = m.candidate_from_model({"起点":"乙","终点":"甲","长度km":"10km"}, 1, "b", [], 2)
        c = m.candidate_from_model({"起点":"甲","终点":"乙","长度km":"12km"}, 1, "c", [], 3)
        rows = m.deduplicate([a,b,c])
        self.assertEqual(len(rows), 2)

    def test_bad_numeric_values(self):
        self.assertIsNotNone(m.parse_length("11.4.2km")[1])
        self.assertEqual(m.parse_cores("24芯"), (24,None))

    def test_tiles_cover_page(self):
        boxes = m.grid_boxes(3,2,.12)
        self.assertEqual(len(boxes), 6)
        self.assertEqual(boxes[0][:2], (0,0))
        self.assertEqual(boxes[-1][2:], (1,1))

    def test_formula_injection_escaped(self):
        self.assertTrue(m._xlsx_safe("=1+2").startswith("'"))

    def test_offline_replay_exports(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            path = root / "mini.pdf"
            doc = pymupdf.open()
            page = doc.new_page(width=500,height=300)
            page.insert_text((30,80),"Station A")
            doc.save(path)
            doc.close()
            replies = root / "replies"
            replies.mkdir()
            (replies / "page001_tile01.txt").write_text(json.dumps({"光缆连接":[{"起点":"Station A","终点":"Station B","长度km":"10km"}],"待核对项":[]}),encoding="utf-8")
            output = root / "result"
            args = m.make_parser().parse_args([str(path),"--output",str(output),"--responses-dir",str(replies),"--cols","1","--rows","1","--ocr","none","--render-long-edge","1400"])
            report = m.run(args)
            self.assertEqual(len(report["records"]),1)
            self.assertEqual(report["records"][0]["status"],"待核对")
            for filename in ("structured.xlsx","review.html","review.csv","candidates.json","ocr.json"):
                self.assertTrue((output / filename).exists(),filename)

if __name__ == "__main__":
    unittest.main()
