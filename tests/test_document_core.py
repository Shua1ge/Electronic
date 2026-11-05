import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from document_core import aggregate, validate_page


def record(id, page, cores, category="光缆"):
    return {"id":id,"page":page,"category":category,"fields":{"芯数":cores,"A端站点":"甲","Z端站点":"乙"},"evidence":"原图文字"}


class ProvenanceTests(unittest.TestCase):
    def test_conflicting_values_are_not_silently_chosen(self):
        result=aggregate([record("A",1,72),record("B",2,36)],{"groups":[{"source_ids":["A","B"]}]})
        row=result["records"][0]
        self.assertIsNone(row["fields"]["芯数"])
        self.assertEqual(len(row["conflicts"]["芯数"]),2)
        self.assertEqual(row["field_sources"]["A端站点"],["A","B"])

    def test_parallel_cables_not_merged_by_matching_endpoints(self):
        result=aggregate([record("A",1,72),record("B",1,72)],{"groups":[]})
        self.assertEqual(len(result["records"]),2)

    def test_unknown_marker_does_not_conflict_with_observed_value(self):
        result=aggregate([record("A",1,"None specified"),record("B",2,72)],{"groups":[{"source_ids":["A","B"]}]})
        row=result["records"][0]
        self.assertEqual(row["fields"]["芯数"],72)
        self.assertEqual(row["field_sources"]["芯数"],["B"])
        self.assertNotIn("芯数",row["conflicts"])

    def test_hallucinated_or_reused_ids_never_drop_records(self):
        result=aggregate([record("A",1,72),record("B",2,72),record("C",3,72)],
                         {"groups":[{"source_ids":["A","UNKNOWN"]},{"source_ids":["A","B"]},{"source_ids":["B","C"]}]})
        ids=[i for row in result["records"] for i in row["source_ids"]]
        self.assertCountEqual(ids,["A","B","C"])
        self.assertEqual(len(result["rejected_proposals"]),2)

    def test_unknown_business_field_rejected(self):
        with self.assertRaises(ValueError):
            validate_page('{"page_kind":"测试","title":null,"records":[{"category":"光缆","fields":{"凭空字段":72},"evidence":"x"}],"unresolved":[]}')

    def test_structured_uncertainty_does_not_drop_valid_facts(self):
        result=validate_page('{"page_kind":"路由图","title":null,"records":[{"category":"光缆","fields":{"芯数":72},"evidence":"72芯","uncertainties":[{"端点":"不清楚"}]}],"unresolved":[{"区域":"右下角","问题":"小字"}]}')
        self.assertEqual(result["records"][0]["fields"]["芯数"],72)
        self.assertIn("右下角",result["unresolved"][0])
        self.assertEqual(len(result["format_normalization"]),2)


if __name__ == "__main__":
    unittest.main()
