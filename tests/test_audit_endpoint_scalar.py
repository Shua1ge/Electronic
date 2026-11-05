import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_corpus import case_matches

class EndpointScalarTests(unittest.TestCase):
    def test_multi_station_route_is_not_a_single_endpoint(self):
        case={'id':'a','category':'光路','source_pages':[2],'endpoints':['甲','乙'],'fields':{}}
        row={'id':'r','category':'光路','page':2,'fields':{'A端站点':'甲变','Z端站点':'乙变～丙变'}}
        self.assertFalse(case_matches(case,row))

if __name__=='__main__':unittest.main()
