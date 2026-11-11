"""Synthetic truth only: annotation persistence, strict contracts and scoring."""
import copy
import json
import io
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from document_annotation import (AnnotationConflict, AnnotationStore, export_project, make_table,
                                 fact_export, new_region, pending, project_init, validate_region, validate_table)
from document_annotation_server import make_handler
from document_eval import execute, pack, parser, prediction_to_page, prepare_case, render_image, score
from document_eval_backend import OpenAICompatibleQwen, compute_dtype_name
from document_eval_core import (evidence_counts, json_hash, match_objects, parse_prediction,
                                read_json, score_case, validate_gold, validate_manifest)
from document_eval_tasks import character_counts, character_summary, score_answers


def fact(value=None, state="visible", unit=""):
    return {**pending(), "state": state, "value": value, "unit": unit}


def node(oid, box, name="Alpha"):
    return {"id": oid, "bbox": box, "kind": "station", "name": name, "name_state": "visible", "parent_id": None, "evidence": []}


def edge(eid="E1"):
    return {"id": eid, "source": "N1", "target": "N2", "relation": "cable", "directed": False, "path": [],
            "attributes": {"cores": fact(24), "length": fact(11.3, unit="km"), "construction": fact(state="not_provided")}, "evidence": []}


def graph():
    return {"region_role": "main_diagram", "objects": [node("N1", [.1, .7, .2, .8]), node("N2", [.7, .7, .8, .8], "Beta")], "connections": [edge()]}


def prediction():
    g = graph()
    return {"region_role": g["region_role"], "objects": [{k: v for k, v in o.items() if k != "name_state"} for o in g["objects"]],
            "connections": [{**{k: e[k] for k in ("id", "source", "target", "relation", "directed", "evidence")},
                             "attributes": {"cores": 24, "length": {"value": 11300, "unit": "m"}, "construction": None}} for e in g["connections"]],
            "answers": [], "uncertainties": []}


def graph_gold():
    g = graph()
    for e in g["connections"]:
        e["attributes"] = {key: fact_export(spec) for key, spec in e["attributes"].items()}
    return {**g, "complete": True, "items": {}}


class ProjectFixture(unittest.TestCase):
    def setUp(self):
        import pymupdf
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        pdf = self.root / "fixture.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=500)
            page.insert_text((30, 50), "Hello 24.")
            page.draw_rect((30, 150, 550, 300))
            page.draw_line((120, 375), (420, 375))
            doc.save(pdf)
        self.project_dir = project_init(pdf, self.root / "project", "TEST")
        self.store = AnnotationStore(self.project_dir)

    def populate(self, reviewed=True):
        text = new_region("T1", 1, [.02, .02, .98, .25], "text")
        text["title"] = "HIDDEN_ANNOTATION_TITLE"
        text["data"].update(transcript="Hello 24.", state="visible")
        text["items"] = [{"id": "Q1", "question": "What is the indicated count?", **fact(24)}]
        table = new_region("T2", 1, [.02, .3, .98, .6], "table")
        table["data"] = make_table(2, 2, [["Name", "Count"], ["Alpha", "24"]])
        diagram = new_region("T3", 1, [.02, .65, .98, .98], "diagram")
        diagram["data"] = graph()
        regions = [text, table, diagram]
        if reviewed:
            for r in regions:
                r.update(status="reviewed", reviewer="synthetic-test")
        return self.store.save(0, regions, [], "synthetic-test")


class AnnotationTests(ProjectFixture):
    def test_three_types_save_reload_and_separate_gold_export(self):
        self.populate()
        out = export_project(self.project_dir, self.root / "export")
        manifest, gold = read_json(out / "manifest.json"), read_json(out / "gold.json")
        self.assertEqual({c["task_type"] for c in manifest["cases"]}, {"text", "table", "diagram"})
        self.assertNotIn("Hello 24.", json.dumps(manifest))
        self.assertNotIn("HIDDEN_ANNOTATION_TITLE", json.dumps(manifest))
        self.assertEqual(gold["cases"]["TEST-T1-A"]["transcript"], "Hello 24.")
        validate_gold(gold, manifest)
        self.assertTrue((self.project_dir / "history/revision-000001.json").is_file())

    def test_drafts_export_null_gold_not_empty_arrays(self):
        self.populate(reviewed=False)
        out = export_project(self.project_dir, self.root / "export")
        gold = read_json(out / "gold.json")
        self.assertEqual(gold["annotation_status"], "pending")
        self.assertTrue(all(g is None for g in gold["cases"].values()))

    def test_editing_reviewed_content_reopens_review(self):
        p = self.populate()
        p["regions"][0]["data"]["transcript"] = "changed"
        saved = self.store.save(1, p["regions"], [], "second-editor")
        self.assertEqual(saved["regions"][0]["status"], "draft")
        self.assertEqual(saved["regions"][1]["status"], "reviewed")

    def test_stale_window_cannot_overwrite(self):
        p = self.populate()
        with self.assertRaises(AnnotationConflict):
            self.store.save(0, p["regions"], [], "stale")

    def test_pending_fields_block_review_but_allow_draft(self):
        r = new_region("T1", 1, [0, 0, 1, 1], "text")
        validate_region(r, 1)
        r.update(status="reviewed", reviewer="test")
        with self.assertRaisesRegex(ValueError, "尚未标注"):
            validate_region(r, 1, reviewed=True)

    def test_source_mutation_blocks_export(self):
        self.populate()
        with (self.project_dir / "source.pdf").open("ab") as f:
            f.write(b"changed")
        with self.assertRaisesRegex(ValueError, "源PDF"):
            export_project(self.project_dir, self.root / "export")

    def test_text_layer_is_only_a_draft(self):
        p = self.populate(False)
        result = self.store.suggest_text("T1")
        self.assertIn("Hello", result["text"])
        self.assertEqual(result["source"], "native_text_draft")
        self.assertEqual(self.store.load(), p)

    def test_table_overlap_and_holes_are_rejected(self):
        table = make_table(2, 2, [["a", "b"], ["c", "d"]])
        table["cells"][0]["colspan"] = 2
        with self.assertRaisesRegex(ValueError, "重叠"):
            validate_table(table)
        table = make_table(2, 2)
        table["cells"].pop()
        with self.assertRaisesRegex(ValueError, "未覆盖"):
            validate_table(table, allow_pending=True)

    def test_cross_page_and_context_references_are_explicit(self):
        p = self.populate()
        for r in p["regions"]:
            r["contexts"] = [{"region_id": "T1" if r["id"] != "T1" else "T2", "purpose": "expert-selected explanation"}]
        self.store.save(1, p["regions"], [], "test")
        out = export_project(self.project_dir, self.root / "export", "C")
        manifest = read_json(out / "manifest.json")
        self.assertTrue(all(c["context_selection"] == "oracle_diagnostic" and len(c["contexts"]) == 2 for c in manifest["cases"]))

    def test_cycles_and_dangling_graph_edges_rejected(self):
        r = new_region("R1", 1, [0, 0, 1, 1], "diagram")
        r["data"] = graph()
        r["data"]["objects"][0]["parent_id"] = "N2"
        r["data"]["objects"][1]["parent_id"] = "N1"
        with self.assertRaisesRegex(ValueError, "循环"):
            validate_region(r, 1)

    def test_changed_context_reopens_dependent_review(self):
        p = self.populate(False)
        p["regions"][2]["contexts"] = [{"region_id": "T1", "purpose": "legend"}]
        p = self.store.save(1, p["regions"], [], "test")
        for r in p["regions"]:
            r.update(status="reviewed", reviewer="test")
        p = self.store.save(2, p["regions"], [], "test")
        p["regions"][0]["data"]["transcript"] = "changed legend"
        p = self.store.save(3, p["regions"], [], "test")
        self.assertEqual(p["regions"][2]["status"], "draft")


class MetricTests(unittest.TestCase):
    def test_iou_assignment_maximizes_cardinality(self):
        p = [{"bbox": [0, 0, .65, 1]}, {"bbox": [0, 0, .4, 1]}]
        g = [{"bbox": [0, 0, .4, 1]}, {"bbox": [.35, 0, .85, 1]}]
        pairs = match_objects(p, g, .3)
        self.assertEqual(len(pairs), 2)
        self.assertEqual({(p, g) for p, g, _ in pairs}, {(0, 1), (1, 0)})

    def test_wrong_name_does_not_change_spatial_matching(self):
        p = prediction()
        p["objects"][0]["name"] = "Wrong"
        result = score_case(p, graph_gold(), .5)
        self.assertEqual(result["objects"]["f1"], 1)
        self.assertEqual(result["object_name_conditional"]["accuracy"], .5)

    def test_duplicate_edges_are_false_positives(self):
        p = prediction()
        p["connections"].append({**copy.deepcopy(p["connections"][0]), "id": "E2"})
        result = score_case(p, graph_gold(), .5)
        self.assertEqual(result["connections"]["fp"], 1)
        self.assertEqual(result["connections"]["tp"], 1)
        self.assertEqual(result["parallel_matches_without_attribute_score"], 1)

    def test_direction_and_relation_must_match(self):
        p, g = prediction(), graph_gold()
        p["connections"][0]["relation"] = "circuit"
        self.assertEqual(score_case(p, g, .5)["connections"]["tp"], 0)
        p = prediction()
        p["connections"][0].update(source="N2", target="N1", directed=True)
        g["connections"][0]["directed"] = True
        self.assertEqual(score_case(p, g, .5)["connections"]["tp"], 0)

    def test_numeric_units_and_unknown_claims(self):
        p = prediction()
        p["connections"][0]["attributes"]["construction"] = "new"
        result = score_case(p, graph_gold(), .5)
        self.assertEqual(result["attributes"]["length"]["end_to_end"]["accuracy"], 1)
        self.assertEqual(result["attributes"]["construction"]["claims_on_unknown_gold"], 1)

    def test_execution_failure_still_counts_missed_graph(self):
        result = score_case(None, graph_gold(), .5)
        self.assertEqual(result["objects"]["fn"], 2)
        self.assertEqual(result["connections"]["fn"], 1)

    def test_abstain_is_not_zero_error_or_correct_omission(self):
        result = score_answers([{"id": "Q", "state": "abstained", "value": None}], {"Q": {"state": "visible", "value": "a"}})
        self.assertEqual(result["coverage"], 0)
        self.assertIsNone(result["selective_error"])
        self.assertEqual(result["omitted"], 0)

    def test_answering_unknown_gold_counts_as_error(self):
        result = score_answers([{"id": "Q", "state": "answered", "value": 24}], {"Q": {"state": "unreadable", "value": None}})
        self.assertEqual(result["selective_error"], 1)

    def test_cer_is_levenshtein_not_similarity_or_accuracy(self):
        r = character_summary(character_counts("A 25!", "A24!"))
        self.assertEqual(r["cer"], .25)
        self.assertEqual(r["exact_match"], 0)

    def test_strict_parser_rejects_nan_duplicate_keys_and_dangling_ids(self):
        case = {"items": []}
        for raw in ('{"region_role":NaN}', '{"region_role":"unknown","region_role":"mixed"}', '```json\n{}\n```'):
            with self.assertRaises(ValueError):
                parse_prediction(raw, case)
        p = prediction()
        p["connections"][0]["source"] = "MISSING"
        with self.assertRaisesRegex(ValueError, "dangling"):
            parse_prediction(json.dumps(p), case)

    def test_reference_validity_is_separate_from_semantic_support(self):
        p = prediction()
        p["objects"][0]["evidence"] = [{"image_id": "invented", "bbox": [0, 0, 1, 1]}]
        self.assertEqual(evidence_counts(p, {"target"}), {"references": 1, "valid_references": 0})

    def test_turing_uses_fp16(self):
        self.assertEqual(compute_dtype_name((7, 5)), "float16")
        self.assertEqual(compute_dtype_name((8, 6)), "bfloat16")

    def test_unknown_and_empty_transcripts_do_not_credit_refusal(self):
        g = {"task_type": "text", "items": {}, "state": "visible", "transcript": ""}
        result = score_case(None, g, .5)
        self.assertEqual(result["transcription"]["exact_match"], 0)
        self.assertIsNone(result["transcription"]["cer"])

    def test_table_merge_errors_and_missing_cells_are_not_text_matches(self):
        g = {**make_table(1, 2, [["A", "B"]]), "task_type": "table", "items": {}}
        p = {"rows": 1, "cols": 2, "header_rows": 1, "answers": [], "cells": [
            {"row": 0, "col": 0, "rowspan": 1, "colspan": 2, "state": "answered", "text": "AB", "evidence": []}]}
        result = score_case(p, g, .5)
        self.assertEqual(result["cell_structure"]["tp"], 0)
        self.assertEqual(result["cell_structure"]["fn"], 2)
        self.assertEqual(result["cell_text_end_to_end"]["cer"], 1)


class InferenceWorkflowTests(ProjectFixture):
    def test_local_server_request_preserves_images_and_generation_metadata(self):
        self.populate()
        out = export_project(self.project_dir, self.root / "export")
        args = parser().parse_args(["run", str(out / "manifest.json"), "--backend", "openai",
                                   "--api-url", "http://127.0.0.1:8000/v1", "--output", str(self.root / "run")])
        backend = OpenAICompatibleQwen(args)
        captured = []
        def response(request, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"model": "server-model", "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
                                         "usage": {"prompt_tokens": 123, "completion_tokens": 50}}).encode())
        image = self.store.page_image(1)
        with patch("document_eval_backend.urllib.request.urlopen", response):
            result = backend.infer("question, no gold", [{"id": "target", "path": str(image)}], 17)
        self.assertEqual(captured[0]["temperature"], 0)
        self.assertTrue(captured[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertTrue(result["max_tokens_reached"])
        self.assertEqual(result["input_tokens"], 123)
        self.assertIsNone(result["peak_reserved_gib"])

    def test_three_task_run_score_replay_and_pack_without_gpu(self):
        self.populate()
        export = export_project(self.project_dir, self.root / "export")
        responses = [
            {"transcript": "Hello 24.", "state": "answered", "evidence": [], "answers": [{"id": "Q1", "state": "answered", "value": 24, "reason": "", "evidence": []}], "uncertainties": []},
            {"rows": 2, "cols": 2, "header_rows": 1, "cells": [
                {k: ("answered" if k == "state" else v) for k, v in c.items() if k != "id"}
                for c in make_table(2, 2, [["Name", "Count"], ["Alpha", "24"]])["cells"]], "answers": [], "uncertainties": []},
            prediction(),
        ]
        test = self
        class Stub:
            metadata = {"backend": "synthetic_test_stub", "model_id": None}
            def __init__(self, args): self.n = 0
            def infer(self, prompt, images, seed):
                test.assertNotIn("HIDDEN_ANNOTATION_TITLE", prompt)
                test.assertNotIn("Hello 24.", prompt)
                reply = copy.deepcopy(responses[self.n])
                if self.n == 2:
                    a, b, c, d, e, f = images[0]["image_to_page_affine"]
                    det = a*e-b*d
                    for obj in reply["objects"]:
                        x0, y0, x1, y1 = obj["bbox"]
                        points = [((e*(x-c)-b*(y-f))/det, (-d*(x-c)+a*(y-f))/det) for x in (x0,x1) for y in (y0,y1)]
                        obj["bbox"] = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
                result = {"raw": json.dumps(reply), "seconds": .01, "input_tokens": 10, "generated_tokens": 10, "max_tokens_reached": False}
                self.n += 1
                return result
            def recover(self): pass
        run_dir = self.root / "run"
        args = parser().parse_args(["run", str(export / "manifest.json"), "--output", str(run_dir), "--max-pixels", "65536"])
        self.assertEqual(execute(args, Stub), 0)
        metrics = read_json(run_dir / "metrics.json")
        self.assertTrue(all(v["accuracy"] is None for v in metrics["groups"].values()))
        scored = self.root / "scored"
        score(parser().parse_args(["score", str(run_dir), "--gold", str(export / "gold.json"), "--output", str(scored)]))
        metrics = read_json(scored / "metrics.json")
        self.assertEqual(metrics["groups"]["TEST/A/text"]["accuracy"]["transcription"]["cer"], 0)
        self.assertEqual(metrics["groups"]["TEST/A/table"]["accuracy"]["cell_structure"]["f1"], 1)
        self.assertEqual(metrics["groups"]["TEST/A/diagram"]["accuracy"]["connections"]["f1"], 1)
        replay = self.root / "replay"
        replay_args = parser().parse_args(["run", str(export / "manifest.json"), "--output", str(replay), "--backend", "replay", "--responses-dir", str(run_dir), "--max-pixels", "65536"])
        self.assertEqual(execute(replay_args), 0)
        self.assertIsNone(read_json(replay / "calls/TEST-T1-A.json")["usage"].get("seconds"))
        package = self.root / "results.zip"
        (run_dir / "do-not-package.pdf").write_bytes(b"private")
        pack(parser().parse_args(["pack", str(run_dir), "--output", str(package)]))
        import zipfile
        with zipfile.ZipFile(package) as z:
            self.assertNotIn("do-not-package.pdf", z.namelist())
            self.assertFalse(any(x.startswith("images/") for x in z.namelist()))
        scored_package = self.root / "scored-results.zip"
        pack(parser().parse_args(["pack", str(run_dir), "--scored-dir", str(scored), "--output", str(scored_package)]))
        with zipfile.ZipFile(scored_package) as z:
            self.assertIn("scored/metrics.json", z.namelist())
        call = run_dir / "calls/TEST-T1-A.json"
        call.write_text(call.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "raw call changed"):
            score(parser().parse_args(["score", str(run_dir), "--gold", str(export / "gold.json"), "--output", str(self.root / "tampered-score")]))

    def test_model_load_failure_is_recorded_and_scored_as_omission(self):
        self.populate()
        export = export_project(self.project_dir, self.root / "export")
        class Failing:
            def __init__(self, args): raise RuntimeError("synthetic CUDA out of memory")
        out = self.root / "failed"
        args = parser().parse_args(["run", str(export / "manifest.json"), "--output", str(out), "--max-pixels", "65536"])
        self.assertEqual(execute(args, Failing), 2)
        metrics = read_json(out / "metrics.json")
        self.assertTrue(all(g["runtime"]["execution_errors"] == 1 for g in metrics["groups"].values()))
        score(parser().parse_args(["score", str(out), "--gold", str(export / "gold.json"), "--output", str(self.root / "scored")]))
        metrics = read_json(self.root / "scored/metrics.json")
        self.assertEqual(metrics["groups"]["TEST/A/text"]["accuracy"]["transcription"]["cer"], 1)
        self.assertEqual(metrics["groups"]["TEST/A/diagram"]["accuracy"]["connections"]["fn"], 1)

    def test_truncated_or_invalid_output_is_retained_and_not_repaired(self):
        self.populate()
        export = export_project(self.project_dir, self.root / "export")
        class Invalid:
            metadata = {"backend": "synthetic_test_stub"}
            def __init__(self, args): pass
            def infer(self, prompt, images, seed): return {"raw": '{"truncated":', "max_tokens_reached": True}
            def recover(self): pass
        out = self.root / "invalid"
        args = parser().parse_args(["run", str(export / "manifest.json"), "--output", str(out), "--max-pixels", "65536"])
        self.assertEqual(execute(args, Invalid), 3)
        metrics = read_json(out / "metrics.json")
        self.assertTrue(all(g["runtime"]["invalid_outputs"] == 1 and g["runtime"]["truncated_responses"] == 1 for g in metrics["groups"].values()))

    def test_rotated_crop_uses_displayed_page_coordinates(self):
        import pymupdf
        from PIL import Image
        with pymupdf.open() as doc:
            p = doc.new_page(width=400, height=200)
            p.draw_rect((0, 0, 200, 200), color=(1, 0, 0), fill=(1, 0, 0))
            p.draw_rect((200, 0, 400, 200), color=(0, 0, 1), fill=(0, 0, 1))
            p.set_rotation(90)
            target = self.root / "crop.png"
            meta = render_image(p, [0, 0, 1, .5], target, 65536, 800)
            with Image.open(target) as image:
                color = image.getpixel((image.width//2, image.height//2))
            self.assertGreater(color[0], 240)
            self.assertLess(color[2], 10)
            self.assertLessEqual(meta["pixels"], 65536)

    def test_extra_rotation_maps_model_boxes_back_to_whole_page(self):
        import pymupdf
        with pymupdf.open(self.project_dir / "source.pdf") as doc:
            meta = render_image(doc[0], [.1, .2, .9, .8], self.root / "rotated.png", 65536, 800, 90)
        p = prediction()
        p["objects"] = [{**p["objects"][0], "bbox": [0, 0, .5, .5]}]
        mapped = prediction_to_page(p, {"task_type": "diagram"}, [meta])
        actual = mapped["objects"][0]["bbox"]
        for a, b in zip(actual, [.1, .5, .5, .8]):
            self.assertAlmostEqual(a, b, delta=.003)


class HTTPTests(ProjectFixture):
    def test_browser_api_persistence_export_and_request_restrictions(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store, "test-token"))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), worker.join()))
        base = f"http://127.0.0.1:{server.server_port}"
        def get(path):
            with urllib.request.urlopen(base+path) as response: return response.read()
        self.assertIn(b"/app.js", get("/"))
        self.assertEqual(json.loads(get("/api/session"))["token"], "test-token")
        self.assertTrue(get("/api/page?page=1").startswith(b"\x89PNG"))
        with self.assertRaises(urllib.error.HTTPError): get("/api/page?page=999")
        with self.assertRaises(urllib.error.HTTPError): get("/../source.pdf")
        region = new_region("T1", 1, [0, 0, 1, 1], "text")
        payload = {"revision": 0, "regions": [region], "links": [], "actor": "http-test"}
        req = urllib.request.Request(base+"/api/save", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "X-Annotation-Token": "test-token"})
        with urllib.request.urlopen(req) as res: self.assertEqual(json.load(res)["revision"], 1)
        self.assertEqual(len(json.loads(get("/api/project"))["regions"]), 1)
        with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(req)


if __name__ == "__main__":
    unittest.main()
