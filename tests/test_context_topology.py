import json
import sys
import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from project_context import identity,stage_of,build_context,build_graph,context_prompt
from drawing_topology import line_paths,detect_ink,symbol_similarity,endpoint_patch,associate_symbols
from channel_geometry import plain_header
from channel_tasks import constrain_endpoints


class ContextTests(unittest.TestCase):
    def test_stage_mixture_stays_unknown(self):
        self.assertEqual(stage_of('利用原有光缆新建临时通道'),'未确定')
        self.assertEqual(stage_of('投运后采用双路由'),'目标')

    def test_parallel_cables_and_protection_sets_are_not_merged(self):
        a={'type':'物理光缆','stage':'目标','attributes':{'回次':'I','A端站点':'甲','Z端站点':'乙'}}
        b={**a,'attributes':{**a['attributes'],'回次':'II'}}
        self.assertEqual(identity(a,b)[0],'不同对象')
        self.assertEqual(identity(a,{**a})[0],'证据不足')
        self.assertEqual(identity(a,{**a,'stage':'现状'})[0],'证据不足')

    def test_context_count_is_task_not_fabricated_objects(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'pages').mkdir();(p/'assets/001').mkdir(parents=True)
            (p/'run.json').write_text(json.dumps({'signature':{'pdf_sha256':'abc'}}))
            (p/'pages/001.json').write_text(json.dumps({'page':1,'route':{'kind':'设计说明'}}))
            (p/'assets/001/ocr.json').write_text(json.dumps({'lines':[{'id':'T1','text':'甲站新建两根72芯OPGW光缆','bbox':[.1,.1,.8,.2]}]}))
            (p/'observations.json').write_text('[]')
            context=build_context(p);g,t=build_graph(p)
            self.assertEqual(context['expectations'][0]['expected_count'],2)
            self.assertEqual(g['objects'],[])
            self.assertEqual(t[0]['kind'],'数量核对')
            self.assertIn('不能据此填写',context_prompt(p,[{'text':'甲站'}]))
            self.assertIn('待验证',context['claims'][0]['status'])

    def test_instruction_context_does_not_supply_local_field_evidence(self):
        c={'a_headers':['a'],'z_headers':['z'],'row_ids':[],'category':'非保护业务',
           'lines':[{'id':'a','text':'甲站'},{'id':'z','text':'乙站'}]}
        r,_=constrain_endpoints({'fields':{'申请业务速率':None},'source_ids':[]},c)
        self.assertIsNone(r['fields']['申请业务速率'])

    def test_reversed_endpoint_orientation_retained(self):
        c={'a_headers':['a'],'z_headers':['z'],'row_ids':[],'category':'非保护业务',
           'lines':[{'id':'a','text':'甲站'},{'id':'z','text':'乙站'}]}
        r,_=constrain_endpoints({'fields':{'A端站点':'乙站','Z端站点':'甲站'},'source_ids':[]},c)
        self.assertEqual((r['fields']['A端站点'],r['fields']['Z端站点']),('乙站','甲站'))

    def test_two_network_captions_do_not_select_first(self):
        c={'a_headers':['a','b'],'z_headers':['z'],'row_ids':[],'category':'非保护业务',
           'lines':[{'id':'a','text':'甲站（主干网1）'},{'id':'b','text':'甲站（主干网2）'},{'id':'z','text':'乙站'}]}
        r,_=constrain_endpoints({'fields':{},'source_ids':[]},c)
        self.assertNotIn('传输系统',r['fields'])


class TopologyTests(unittest.TestCase):
    def test_faint_stroke_survives(self):
        gray=np.array([[255,239,220,80]],dtype=np.uint8)
        self.assertEqual(detect_ink(gray).tolist(),[[0,255,255,255]])

    def test_fold_preserves_real_endpoints(self):
        paths,ambiguous=line_paths([[200,500,800,500],[200,520,600,520]],[[200,500,200,520]],[],1000,1000)
        self.assertEqual(len(paths),1)
        self.assertEqual(paths[0]['points'][0],[.8,.5]);self.assertEqual(paths[0]['points'][-1],[.6,.52])

    def test_crossing_does_not_join_two_paths(self):
        paths,_=line_paths([[100,500,900,500],[100,600,900,600]],[[500,400,500,700]],[],1000,1000)
        self.assertEqual(len(paths),2)

    def test_ambiguous_fold_not_selected(self):
        paths,ambiguous=line_paths([[200,500,800,500],[200,520,600,520],[200,530,700,530]],[[200,500,200,520],[200,500,200,530]],[],1000,1000)
        self.assertEqual(len(paths),3);self.assertTrue(ambiguous)

    def test_plain_station_caption(self):
        rows=[{'id':'a','text':'永阳','bbox':[.38,.35,.42,.38]}]
        self.assertEqual(plain_header([.39,.4,.41,.6],rows),rows)

    def test_legend_conflicts_abstain(self):
        gray=np.full((300,300),255,dtype=np.uint8);cv2.circle(gray,(90,90),5,0,-1)
        patch=endpoint_patch(gray,.3,.3).tolist()
        entries=[{'symbol':'endpoint_sample','patches':[patch],'category':'保护业务','rate':'2M'},
                 {'symbol':'endpoint_sample','patches':[patch],'category':'非保护业务','rate':'2M'}]
        self.assertEqual(associate_symbols(gray,[(.3,.3),(.3,.3)],entries),[])

    def test_blank_symbols_cannot_match(self):
        self.assertEqual(symbol_similarity(np.zeros((25,25)),np.zeros((25,25))),0)

if __name__=='__main__':unittest.main()

class HeaderRegressionTests(unittest.TestCase):
    def test_plain_caption_beats_network_heading(self):
        from channel_geometry import header_for_box
        rows=[{'id':'network','text':'省干网1','bbox':[.35,.10,.44,.12]},
              {'id':'station','text':'丙站','bbox':[.39,.15,.41,.17]}]
        result=header_for_box([.39,.19,.42,.40],rows)
        self.assertEqual(result[0]['id'],'station')

    def test_business_label_is_not_station_caption(self):
        from channel_geometry import header_for_box
        rows=[{'id':'business','text':'地调调度数据网通道','bbox':[.35,.13,.44,.15]},
              {'id':'station','text':'永阳','bbox':[.39,.17,.41,.19]}]
        result=header_for_box([.39,.21,.42,.40],rows)
        self.assertEqual([r['id'] for r in result],['station'])

    def test_targeted_review_crop_keeps_fold_extent(self):
        from review_evidence import task_bbox
        task={'geometry':{'path':[[.8,.5],[.2,.5],[.2,.52],[.6,.52]]}}
        self.assertEqual(task_bbox(task),[.2,.5,.8,.52])

class PlainEndpointTests(unittest.TestCase):
    def test_plain_station_corrects_same_terminal_error(self):
        c={'a_headers':['a'],'z_headers':['z'],'row_ids':[],'category':'非保护业务',
           'lines':[{'id':'a','text':'丙站'},{'id':'z','text':'项目D变'}]}
        raw={'fields':{'A端站点':'项目D变','Z端站点':'项目D变'},'source_ids':[]}
        r,_=constrain_endpoints(raw,c)
        self.assertEqual((r['fields']['A端站点'],r['fields']['Z端站点']),('丙站','项目D变'))
        self.assertEqual(raw['fields']['A端站点'],'项目D变')

    def test_network_caption_cannot_be_plain_station(self):
        from channel_tasks import station_part
        self.assertIsNone(station_part('某地区地区光传输网'))
