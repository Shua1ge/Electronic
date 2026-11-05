import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from channel_geometry import containing_node,join_collinear,header_for_box
from channel_tasks import constrain_endpoints


class ChannelTests(unittest.TestCase):
    def test_crossed_box_is_not_destination(self):
        boxes=[[.1,.1,.2,.8],[.4,.1,.5,.8],[.8,.1,.9,.8]]
        self.assertEqual(containing_node((.85,.4),boxes),boxes[2])
        self.assertIsNone(containing_node((.7,.4),boxes))

    def test_port_gap_joins_but_parallel_lines_stay_separate(self):
        lines=[[100,100,300,100],[307,100,700,100],[100,110,700,110]]
        self.assertEqual(join_collinear(lines,1000,1000),[[100,100,700,100],[100,110,700,110]])

    def test_inline_caption_ignores_business_destination_label(self):
        lines=[{'id':'a','text':'甲站路由器','bbox':[.15,.46,.29,.49]},
               {'id':'b','text':'甲站至乙变线路保护','bbox':[.16,.42,.29,.44]}]
        self.assertEqual(header_for_box([.3,.43,.34,.53],lines)[0]['id'],'a')

    def test_endpoint_constraint_preserves_raw_and_corroborates_row(self):
        rows=[{'id':'A','text':'甲站（骨干网1）'},{'id':'Z','text':'丁站变（骨干网1）'},
              {'id':'R','text':'甲站至丁站变：测距'},{'id':'S','text':'2M 数字通道'}]
        c={'a_headers':['A'],'z_headers':['Z'],'row_ids':['R','S'],'lines':rows,'category':'非保护业务'}
        raw={'fields':{'A端站点':'甲站','Z端站点':'中途站','申请业务速率':None},'source_ids':['R']}
        result,fixes=constrain_endpoints(raw,c)
        self.assertEqual(result['fields']['Z端站点'],'丁站变')
        self.assertEqual(result['fields']['申请业务速率'],'2M')
        self.assertEqual(raw['fields']['Z端站点'],'中途站')
        self.assertEqual(raw['source_ids'],['R'])
        self.assertTrue(fixes)

    def test_conflicting_caption_readings_are_not_silently_resolved(self):
        c={'a_headers':['A'],'z_headers':['Z','V'],'row_ids':[], 'category':'非保护业务',
           'lines':[{'id':'A','text':'甲站'},{'id':'Z','text':'乙站'},{'id':'V','text':'丙站'}]}
        r,_=constrain_endpoints({'fields':{'Z端站点':'乙站'},'source_ids':[]},c)
        self.assertEqual(r['fields']['Z端站点'],'乙站')


if __name__=='__main__':unittest.main()
