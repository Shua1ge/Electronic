"""Offline regression tests for shared graph, local edits and export parity."""
import copy
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from drawing_graph import bridge_gaps, build_page, export_xlsx, qualified_name, trace_paths, write_json
from drawing_pipeline import apply_labels, apply_name, extract, json_reply, parse_attributes, parser
from review_server import ConflictError, ReviewStore, apply_operations, make_handler


def obj(oid,box,kind="equipment",parent=None,name=None):
    return {"id":oid,"page":1,"kind":kind,"bbox":box,"center":[(box[0]+box[2])/2,(box[1]+box[3])/2],
            "parent_id":parent,"name":name,"name_options":[],"name_state":"qwen_candidate","source":"geometry"}


def fixture():
    objects=[obj("A",[.1,.1,.2,.4],name="柜甲"),obj("B",[.7,.1,.8,.4],name="柜乙"),
             obj("A1",[.19,.19,.2,.21],"port","A","1"),obj("B1",[.7,.19,.71,.21],"port","B","1")]
    path={"id":"P1","points":[[.2,.2],[.5,.2],[.5,.3],[.7,.3]],"segment_ids":["S1","S2","S3"],"a_object":"A","z_object":"B","branch":False,"state":"bound"}
    conn={"id":"C1","page":1,"a":"A1","z":"B1","path_id":"P1","relation":"wiring","attributes":{"cores":24},
          "attribute_evidence":[],"attribute_state":"spatial_candidate","status":"candidate","notes":[],"source":"geometry"}
    return {"version":"test","revision":0,"input":{"name":"synthetic.pdf","sha256":"test"},"events":[],
            "pages":[{"id":1,"kind":"cabinet","size_pt":[500,300],"objects":objects,"connections":[conn],"paths":[path],"image":"pages/page001.png"}]}


class GeometryTests(unittest.TestCase):
    def test_unique_collinear_gap_is_explicit_hypothesis(self):
        ss=[{"id":"S1","a":[.1,.2],"b":[.3,.2]}, {"id":"S2","a":[.308,.2],"b":[.5,.2]}]
        augmented=bridge_gaps(ss,[],"cabinet")
        self.assertEqual(len(augmented),3)
        self.assertEqual(augmented[-1]["method"],"collinear_gap_hypothesis")
        self.assertEqual(len(trace_paths(augmented,[],"cabinet")),1)

    def test_orthogonal_path_survives_without_endpoint_names(self):
        objects=[obj("A",[.1,.1,.2,.3]),obj("B",[.7,.2,.8,.4])]
        ss=[{"id":"S1","a":[.2,.2],"b":[.5,.2]},{"id":"S2","a":[.5,.2],"b":[.5,.3]},
            {"id":"S3","a":[.5,.3],"b":[.7,.3]}]
        paths=trace_paths(ss,objects,"cabinet")
        self.assertEqual(len(paths),1);self.assertEqual(paths[0]["a_object"],"A");self.assertEqual(paths[0]["z_object"],"B")
        self.assertEqual(len(paths[0]["points"]),4)

    def test_crossing_does_not_create_a_turn(self):
        ss=[{"id":"S1","a":[.1,.5],"b":[.9,.5]},{"id":"S2","a":[.5,.1],"b":[.5,.9]}]
        paths=trace_paths(ss,[],"cabinet")
        self.assertEqual(len(paths),2)
        self.assertTrue(all(len(p["points"])==2 for p in paths))

    def test_duplicate_paint_does_not_create_branch(self):
        ss=[{"id":"S1","a":[.1,.5],"b":[.5,.5]},{"id":"S2","a":[.1001,.5],"b":[.5001,.5]},
            {"id":"S3","a":[.5,.5],"b":[.8,.5]}]
        paths=trace_paths(ss,[],"cabinet")
        self.assertEqual(len(paths),1);self.assertFalse(paths[0]["branch"])

    def test_pdf_enclosure_hierarchy_and_port_ends(self):
        import pymupdf
        doc=pymupdf.open();p=doc.new_page(width=600,height=400)
        p.draw_rect((60,70,170,300));p.draw_rect((400,70,520,300))
        p.draw_line((170,160),(280,160));p.draw_line((280,160),(280,210));p.draw_line((280,210),(400,210))
        page=build_page(p,1,"cabinet");doc.close()
        self.assertGreaterEqual(sum(o["kind"]=="equipment" for o in page["objects"]),2)
        self.assertTrue(page["connections"])
        index={o["id"]:o for o in page["objects"]}
        for c in page["connections"]:
            self.assertEqual(index[c["a"]]["kind"],"port")
            self.assertIsNotNone(index[c["a"]]["parent_id"])


class ModelTests(unittest.TestCase):
    def test_repeated_primary_is_not_an_alternative_reading(self):
        o=obj("A",[.1,.1,.2,.2]);apply_name(o,{"name":"甲站","alternatives":["甲站"]},"call")
        self.assertEqual(o["name_state"],"qwen_candidate");self.assertEqual(o["name_options"],[])

    def test_valid_label_array_is_normalized_without_repair(self):
        data=json_reply('[{"text":"24芯 22km"}]',allow_label_list=True)
        self.assertEqual(data["labels"][0]["text"],"24芯 22km")
        with self.assertRaises(ValueError):json_reply('[{"text":"24芯 22km"}')

    def test_uncertainty_is_retained_without_blocking_geometry(self):
        o=obj("A",[.1,.1,.2,.2]);apply_name(o,{"name":"甲站","unclear":True,"alternatives":["乙站"]},"call")
        self.assertEqual(o["id"],"A");self.assertEqual(o["name"],"甲站");self.assertEqual(o["name_state"],"uncertain")

    def test_truncated_model_json_is_not_repaired(self):
        with self.assertRaises(ValueError):json_reply('{"name":"甲站"')

    def test_numeric_values_are_not_substring_evidence(self):
        self.assertEqual(parse_attributes("OPGW 124芯 116.0km"),{"type":"OPGW","cores":124,"length_km":116.0})
        self.assertNotIn("length_km",parse_attributes("端口16"))
        self.assertEqual(parse_attributes("24芯OPGW光缆22km"),{"type":"OPGW","cores":24,"length_km":22.0})

    def test_competing_path_does_not_verify_attribute_association(self):
        graph=fixture();p=graph["pages"][0];c=p["connections"][0]
        p["paths"].append({"id":"P2","points":[[.2,.205],[.7,.205]],"state":"bound"})
        apply_labels(p,c,{"labels":[{"text":"24芯 22km","bbox":[300,190,400,215]}]},[0,0,1,1],"call")
        self.assertEqual(c["attribute_state"],"ambiguous");self.assertEqual(c["status"],"candidate")
        self.assertEqual(c["attributes"]["length_km"],22)

    def test_nested_valid_label_envelopes_preserve_actual_text(self):
        p=fixture()["pages"][0];c=p["connections"][0]
        apply_labels(p,c,{"labels":[{"labels":[{"text":"24芯OPGW 22km","bbox":[200,100,500,300]}]}]},[0,0,1,1],"call")
        self.assertEqual(c["attributes"]["length_km"],22)
        self.assertEqual(c["attribute_evidence"][0]["text"],"24芯OPGW 22km")

    def test_wiring_identifiers_are_kept_without_invented_cable_fields(self):
        p=fixture()["pages"][0];c=p["connections"][0]
        apply_labels(p,c,{"labels":[{"text":"Q0301~Q0302","bbox":[200,100,500,300]}]},[0,0,1,1],"call")
        self.assertEqual(c["attributes"],{"label":"Q0301~Q0302"})


class EditTests(unittest.TestCase):
    def test_repeated_port_numbers_are_scoped_by_parent(self):
        p=fixture()["pages"][0]
        self.assertEqual(qualified_name(p,"A1"),"柜甲 / 1");self.assertEqual(qualified_name(p,"B1"),"柜乙 / 1")

    def test_name_edit_propagates_and_reopens_previous_approval(self):
        graph=fixture();graph["pages"][0]["connections"][0]["status"]="approved"
        edited=apply_operations(graph,0,[{"action":"object_update","page":1,"id":"A","changes":{"name":"新柜名"}}])
        self.assertEqual(qualified_name(edited["pages"][0],"A1"),"新柜名 / 1")
        self.assertEqual(edited["pages"][0]["connections"][0]["status"],"corrected")
        self.assertEqual(graph["revision"],0)

    def test_endpoint_change_invalidates_old_path_and_attribute_claim(self):
        edited=apply_operations(fixture(),0,[{"action":"connection_update","page":1,"id":"C1","changes":{"z":"B"}}])
        c=edited["pages"][0]["connections"][0]
        self.assertIsNone(c["path_id"]);self.assertEqual(c["attribute_state"],"needs_recheck")
        self.assertEqual(c["attributes"]["cores"],24)

    def test_stale_write_rejected(self):
        with self.assertRaises(ConflictError):apply_operations(fixture(),1,[{"action":"status","page":1,"ids":["C1"],"status":"approved"}])

    def test_batch_approval_is_explicit_human_event(self):
        edited=apply_operations(fixture(),0,[{"action":"status","page":1,"ids":["C1"],"status":"approved"}])
        self.assertEqual(edited["pages"][0]["connections"][0]["status"],"approved")
        self.assertEqual(edited["events"][0]["actor"],"local-human-review")

    def test_hierarchy_cycles_and_self_loops_rejected_atomically(self):
        graph=fixture()
        with self.assertRaises(ValueError):apply_operations(graph,0,[{"action":"object_update","page":1,"id":"A","changes":{"parent_id":"A1"}}])
        with self.assertRaises(ValueError):apply_operations(graph,0,[{"action":"connection_update","page":1,"id":"C1","changes":{"z":"A1"}}])
        self.assertEqual(graph,fixture())

    def test_excel_reads_current_graph_names_and_status(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as tmp:
            g=apply_operations(fixture(),0,[{"action":"object_update","page":1,"id":"A","changes":{"name":"新柜名"}},
                                           {"action":"status","page":1,"ids":["C1"],"status":"approved"}])
            path=Path(tmp)/"review.xlsx";export_xlsx(g,path)
            wb=load_workbook(path);ws=wb["连接候选"]
            self.assertEqual(ws.cell(2,5).value,"新柜名 / 1");self.assertEqual(ws.cell(2,11).value,"approved");wb.close()


class ServerTests(unittest.TestCase):
    def test_http_save_reload_export_and_path_restrictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_json(root/"graph.json",fixture());write_json(root/"initial_graph.json",fixture())
            store=ReviewStore(root);server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(store,"test-token"))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f"http://127.0.0.1:{server.server_port}"
            try:
                body=json.dumps({"revision":0,"operations":[{"action":"object_update","page":1,"id":"A","changes":{"name":"持久化柜名"}}]}).encode()
                bad=urllib.request.Request(base+"/api/save?project=drawing",body,headers={"Content-Type":"application/json"})
                with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(bad)
                self.assertEqual(err.exception.code,403)
                good=urllib.request.Request(base+"/api/save?project=drawing",body,headers={"Content-Type":"application/json","X-Review-Token":"test-token"})
                with urllib.request.urlopen(good) as response:self.assertEqual(json.load(response)["revision"],1)
                self.assertEqual(json.loads((root/"graph.json").read_text())["pages"][0]["objects"][0]["name"],"持久化柜名")
                self.assertEqual(json.loads((root/"initial_graph.json").read_text())["revision"],0)
                with urllib.request.urlopen(base+"/api/export?project=drawing") as response:self.assertTrue(response.read().startswith(b"PK"))
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+"/files/drawing/../../outside")
            finally:server.shutdown();server.server_close();thread.join()


class ReplayTests(unittest.TestCase):
    def test_full_pdf_replay_and_excel_share_candidate_graph(self):
        import pymupdf
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pdf=root/"synthetic.pdf";responses=root/"replies";responses.mkdir()
            doc=pymupdf.open();p=doc.new_page(width=600,height=400)
            p.draw_rect((60,70,170,300));p.draw_rect((400,70,520,300));p.draw_line((170,160),(400,160))
            pg=build_page(p,1,"cabinet");doc.save(pdf);doc.close()
            self.assertTrue(pg["connections"])
            for o in pg["objects"]:write_json(responses/(o["id"]+"-name.json"),{"name":"设备甲" if o["kind"]!="port" else "1","alternatives":[],"unclear":False})
            for c in pg["connections"]:write_json(responses/(c["id"]+"-label.json"),{"labels":[{"text":"OPGW 24芯 22km","bbox":[200,100,500,300],"uncertain":False}]})
            args=parser().parse_args([str(pdf),"--output",str(root/"batch"),"--mode","replay","--responses-dir",str(responses),"--kind","cabinet"])
            graph=extract(pdf,root/"run",args)
            saved=json.loads((root/"run/graph.json").read_text());self.assertEqual(graph,saved)
            self.assertEqual(graph["issues"],[]);self.assertTrue(all(c["status"]=="candidate" for c in graph["pages"][0]["connections"]))
            wb=load_workbook(root/"run/structured.xlsx");ws=wb["连接候选"]
            self.assertEqual(ws.max_row-1,len(pg["connections"]));self.assertEqual(ws.cell(2,4).value,graph["pages"][0]["connections"][0]["a"]);wb.close()


if __name__=="__main__":unittest.main()
