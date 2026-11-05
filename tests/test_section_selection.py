import json
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from evidence_pdf import select_context,select_units,task_prompt


class SectionSelectionTests(unittest.TestCase):
    def test_adjacent_sections_do_not_contaminate_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);p=out/'assets/001';p.mkdir(parents=True)
            Image.new('RGB',(200,200),'white').save(p/'ocr.png')
            texts=['一.通信方案','1.光缆方案：','甲站至乙站新建光缆。',
                   '2.光纤电路方案：','甲站与乙站的传输电路。','3.保护通道组织',
                   '第一套保护通道A。','2）测距路由如下：','甲站至乙站测距。','4.光纤通信设备配置：','新增电源设备。']
            lines=[{'id':f'T{i:03d}','text':t,'bbox':[.1,.04*i,.4,.04*i+.03]} for i,t in enumerate(texts)]
            assets={'lines':lines,'overview':'overview.png','detail_views':[{'image':'quad.png'}]}
            selected,images=select_context('设计说明','保护业务',assets,out,1)
            self.assertEqual([x['text'] for x in selected],texts[5:7])
            self.assertTrue(Path(images[1]).is_file())
            selected,_=select_context('设计说明','非保护业务',assets,out,1)
            self.assertEqual([x['text'] for x in selected],texts[7:9])

    def test_unknown_layout_falls_back_to_full_evidence(self):
        assets={'lines':[{'id':'T1','text':'无章节的说明','bbox':[0,0,1,1]}],
                'overview':'overview.png','detail_views':[{'image':'quad.png'}]}
        selected,images=select_context('设计说明','光缆',assets,Path('/unused'),1)
        self.assertEqual(selected,assets['lines'])
        self.assertEqual(images,['overview.png','quad.png'])

    def test_numbered_optical_sections_split_named_planes_without_other_paragraph(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);p=out/'assets/001';p.mkdir(parents=True)
            Image.new('RGB',(500,500),'white').save(p/'ocr.png')
            texts=['2.光纤电路方案：','1）甲站接入主干网一、二平面（甲牌、乙牌），',
                   '甲站至乙站两条2.5Gb/s光纤电路。','2）甲站接入地区城网，速率10Gbit/s。']
            lines=[{'id':f'T{i}','text':t,'bbox':[.1,.1+i*.12,.8,.2+i*.12]} for i,t in enumerate(texts)]
            assets={'lines':lines,'overview':'unused.png','detail_views':[]}
            units=select_units('设计说明','光路',assets,out,1)
            self.assertEqual(len(units),3)
            self.assertTrue(all(len(images)==1 for _,_,images in units))
            self.assertEqual([r['text'] for r in units[2][1]],[texts[-1]])
            self.assertIn('本次仅提取：一平面（甲牌）',task_prompt('光路',units[0][1],units[0][0],out,1))


if __name__=='__main__':unittest.main()
