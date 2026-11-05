import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from channel_tasks import constrain_endpoints
from evidence_pdf import select_units
from routing_rules import resolve_route

class PostValidationFixTests(unittest.TestCase):
    def test_ambiguous_ascii_rate_token_cannot_become_zero_bandwidth(self):
        c={'category':'非保护业务','a_headers':['A'],'z_headers':['Z'],'row_ids':['R'],
           'lines':[{'id':'A','text':'甲站'},{'id':'Z','text':'乙站'},{'id':'R','text':'I0M'}]}
        raw={'fields':{},'source_ids':[],'uncertainties':[]}
        fixed,_=constrain_endpoints(raw,c)
        self.assertNotIn('申请业务速率',fixed['fields'])
        self.assertTrue(fixed['uncertainties'])
        self.assertEqual(raw,{'fields':{},'source_ids':[],'uncertainties':[]})
        for text in ['20M','2X2M','FE口/2M业务通道']:
            c['lines'][-1]['text']=text
            fixed,_=constrain_endpoints(raw,c)
            self.assertEqual(fixed['fields']['申请业务速率'],'2M' if text.startswith('FE') else text)

    def test_numbered_whole_page_fallback_is_not_optical_section(self):
        assets={'lines':[{'id':'T1','text':'1）新建光缆','bbox':[.1,.2,.8,.3]},
                         {'id':'T2','text':'2）安装设备','bbox':[.1,.4,.8,.5]}],
                'overview':'unused.png','detail_views':[]}
        units=select_units('设计说明','光路',assets,Path('/unused'),1)
        self.assertEqual([u[0] for u in units],['whole'])

    def test_verified_channel_allocation_title_routes_to_business(self):
        title='光纤电路2Mb/s通道分配图'
        route=resolve_route({'kind':'光缆路由','title':title},[{'id':'T1','text':title,'bbox':[.65,.9,.95,.95]}])
        self.assertEqual(route['kind'],'业务组织')
        self.assertEqual(route['routing_override']['source_ids'],['T1'])

if __name__=='__main__':unittest.main()
