import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from evidence_core import literal_supported, safe_aggregate, validate_records, explicit_field_normalizations


class EvidenceTests(unittest.TestCase):
    def test_explicit_tokens_map_to_columns_without_guessing(self):
        fields={'业务名称':'第一套保护 ABC-123-X-G','保护装置型号':None}
        self.assertEqual(explicit_field_normalizations('保护业务',fields,'第一套保护 ABC-123-X-G'),{'保护装置型号':'ABC-123-X-G'})
        self.assertEqual(explicit_field_normalizations('保护业务',fields,'第一套保护 ABC-456-Y-G'),{})
        self.assertEqual(explicit_field_normalizations('非保护业务',{'业务名称':'IP电话','中途通道资源使用情况':'FE口/2M业务通道'},'FE口/2M业务通道'),{'申请业务速率':'2M'})

    def test_numeric_data_channel_is_not_automatically_line_protection(self):
        lines=[{'id':'T1','text':'调度数据网 2M数字通道','bbox':[0,0,1,1]}]
        raw=json.dumps({'records':[{'fields':{'业务名称':'2M数字通道'},'source_ids':['T1']}]})
        result=validate_records(raw,'保护业务',lines)
        self.assertEqual(result['records'],[])
        self.assertEqual(len(result['rejected_records']),1)

    def test_missing_citation_can_only_use_a_unique_exact_context_match(self):
        raw=json.dumps({'records':[{'fields':{'A端站点':'甲站'},'source_ids':[]}]})
        lines=[{'id':'T1','text':'甲站','bbox':[0,0,.2,.2]}]
        r=validate_records(raw,'光缆',lines)['records'][0]
        self.assertIn('unique_context',r['field_evidence']['A端站点']['check'])
        self.assertEqual(validate_records(raw,'光缆',lines+[{'id':'T2','text':'甲站','bbox':[.5,.5,1,1]}])['records'],[])

    def test_bad_field_does_not_drop_other_records_or_fields(self):
        lines=[{"id":"T001","text":"甲站—乙站 OPGW 72芯 12km","bbox":[0,.1,1,.2]}]
        raw=json.dumps({"records":[{"fields":{"芯数":128,"光缆类型":"OPGW","数量":2},
                                    "citations":{"芯数":["T001"],"光缆类型":["T001"]}}]})
        r=validate_records(raw,"光缆",lines)["records"][0]
        self.assertEqual(r["fields"],{"光缆类型":"OPGW"})
        self.assertIn("芯数",r["withheld_fields"])
        self.assertIn("数量",r["withheld_fields"])
        self.assertEqual(r["field_evidence"]["光缆类型"]["sources"][0]["bbox"],[0,.1,1,.2])

    def test_unknown_source_does_not_corroborate_a_fact(self):
        r=validate_records('{"records":[{"fields":{"芯数":72},"citations":{"芯数":["T999"]}}]}',"光缆",[])
        self.assertEqual(r["records"],[])
        self.assertEqual(len(r["rejected_records"]),1)

    def test_digit_substrings_are_not_evidence(self):
        self.assertFalse(literal_supported(72,"172芯"))
        self.assertFalse(literal_supported("72km","172km"))
        self.assertFalse(literal_supported("2M","12M"))
        self.assertFalse(literal_supported("2×2M","12X2M"))
        self.assertTrue(literal_supported("2×2M","2X2M（临时通道）"))
        self.assertTrue(literal_supported("2.5Gbit/s","SDH 2.5Gb/s"))

    def test_compact_record_sources_are_mapped_to_field_evidence(self):
        lines=[{"id":"T001","text":"甲站—乙站 OPGW 72芯","bbox":[0,0,1,1]}]
        raw=json.dumps({"records":[{"fields":{"A端站点":"甲站","芯数":72,"光缆长度":"99km"},"source_ids":["T001"]}]})
        row=validate_records(raw,"光缆",lines)["records"][0]
        self.assertEqual(row["fields"],{"A端站点":"甲站","芯数":72})
        self.assertIn("光缆长度",row["withheld_fields"])

    def test_parallel_cables_and_optical_systems_survive_aggregation(self):
        rows=[]
        for n,(cat,fields) in enumerate([
            ("光缆",{"A端站点":"甲","Z端站点":"乙","芯数":72}),
            ("光缆",{"A端站点":"甲","Z端站点":"乙","芯数":72}),
            ("光路",{"A端站点":"甲","Z端站点":"乙","申请光路速率":"2.5Gb/s"}),
            ("光路",{"A端站点":"甲","Z端站点":"乙","申请光路速率":"10Gb/s"})]):
            rows.append({"id":str(n),"page":n+1,"category":cat,"fields":fields,"evidence":"原始证据"})
        result=safe_aggregate(rows)
        self.assertEqual(len(result["records"]),4)
        self.assertEqual([r["fields"].get("申请光路速率") for r in result["records"][-2:]], ["2.5Gb/s","10Gb/s"])

    def test_project_only_dedup_preserves_sources(self):
        rows=[{"id":str(i),"page":i+1,"category":"项目","fields":{"项目名称":"甲工程","设计院名称":"乙院"},"evidence":"原图"} for i in range(2)]
        result=safe_aggregate(rows)
        self.assertEqual(len(result["records"]),1)
        self.assertEqual(result["records"][0]["source_ids"],["0","1"])


if __name__=="__main__":unittest.main()
