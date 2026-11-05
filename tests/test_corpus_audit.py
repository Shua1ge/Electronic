import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_corpus import matching


class CorpusAuditTests(unittest.TestCase):
    def test_one_candidate_cannot_satisfy_two_parallel_source_channels(self):
        cases=[{'id':i,'source_pages':[1],'category':'非保护业务','endpoints':['甲','乙'],
                'fields':{'申请业务速率':'^2M$'}} for i in ['one','two']]
        rows=[{'id':'R1','page':1,'category':'非保护业务','fields':{'A端站点':'甲站','Z端站点':'乙站','申请业务速率':'2M'}}]
        self.assertEqual(len(matching(cases,rows)),1)

    def test_transit_node_or_wrong_rate_does_not_pass_full_check(self):
        cases=[{'id':'one','source_pages':[1],'category':'非保护业务','endpoints':['甲','乙'],
                'fields':{'申请业务速率':'^2M$'}}]
        rows=[{'id':'R1','page':1,'category':'非保护业务','fields':{'A端站点':'甲站','Z端站点':'中途站','申请业务速率':'2M'}},
              {'id':'R2','page':1,'category':'非保护业务','fields':{'A端站点':'甲站','Z端站点':'乙站','申请业务速率':'20M'}}]
        self.assertEqual(matching(cases,rows),{})
        self.assertEqual(matching(cases,rows,True),{'one':'R2'})


if __name__=='__main__':unittest.main()
