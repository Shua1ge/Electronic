import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from routing_rules import resolve_route


class RoutingTests(unittest.TestCase):
    def test_business_title_overrides_equipment_appearance(self):
        route={'title':'业务带宽分配图','kind':'光通信系统'}
        result=resolve_route(route,[{'id':'T1','text':'业务带宽分配图','bbox':[.8,.9,.95,.95]}])
        self.assertEqual(result['kind'],'业务组织')
        self.assertEqual(result['model_kind'],'光通信系统')
        self.assertEqual(result['routing_override']['source_ids'],['T1'])

    def test_body_labels_or_guessed_title_do_not_override(self):
        route={'title':'保护通道图','kind':'光通信系统'}
        self.assertEqual(resolve_route(route,[{'id':'T1','text':'保护通道图','bbox':[0,.2,.5,.3]}]),route)
        self.assertEqual(resolve_route(route,[{'id':'T1','text':'光通信系统配置图','bbox':[.8,.9,.95,.95]}]),route)


if __name__=='__main__':unittest.main()
