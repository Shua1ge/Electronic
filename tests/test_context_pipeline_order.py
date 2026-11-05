"""Verify actual orchestration with fake inference, not its implementation text."""
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import evidence_pdf

class SchedulingTests(unittest.TestCase):
    def test_later_notes_are_read_before_earlier_diagram_and_not_reinferred(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);pdf=root/'input.pdf';out=root/'run';doc=pymupdf.open();doc.new_page();doc.new_page();doc.save(pdf);doc.close();seen=[]
            def prepare(page,n,folder):
                a={'lines':[{'id':'T1','text':'甲站新建两根72芯OPGW' if n==2 else '甲站光缆路由','bbox':[.1,.1,.8,.2]}],
                   'overview':str(folder/'pages'/f'{n:03d}.png'),'seconds':0,'detail_views':[]}
                evidence_pdf.save(folder/'assets'/f'{n:03d}'/'ocr.json',a);return a
            def generate(model,processor,prompt,images,**kwargs):
                n=int(Path(images[0]).stem);routing='先判断页面类型' in prompt
                seen.append((n,routing,prompt))
                answer={'kind':'设计说明' if n==2 else '光缆路由','title':'说明' if n==2 else '路由','project':{},'uncertainties':[]} if routing else {'records':[],'unresolved':[]}
                return types.SimpleNamespace(text=json.dumps(answer),generation_tokens=10,prompt_tokens=10)
            mx=types.ModuleType('mlx.core');mx.random=types.SimpleNamespace(seed=lambda _:None);mx.clear_cache=lambda:None;mx.get_peak_memory=lambda:0
            mlx=types.ModuleType('mlx');mlx.core=mx
            vlm=types.ModuleType('mlx_vlm');vlm.load=lambda _: (None,types.SimpleNamespace(tokenizer=None));vlm.generate=generate
            prompt=types.ModuleType('mlx_vlm.prompt_utils');prompt.apply_chat_template=lambda p,c,text,**kw:text
            utils=types.ModuleType('mlx_vlm.utils');utils.load_config=lambda _:{}
            structured=types.ModuleType('mlx_vlm.structured');structured.build_json_schema_logits_processor=lambda *a:None
            mods={'mlx':mlx,'mlx.core':mx,'mlx_vlm':vlm,'mlx_vlm.prompt_utils':prompt,'mlx_vlm.utils':utils,'mlx_vlm.structured':structured}
            with patch.dict(sys.modules,mods),patch.object(evidence_pdf,'prepare',prepare),patch.object(evidence_pdf,'select_units',lambda kind,cat,a,o,n:[('whole',a['lines'],[a['overview']])]),patch('export_document.export_run'),patch.object(sys,'argv',['evidence_pdf.py',str(pdf),'--output',str(out)]),contextlib.redirect_stdout(io.StringIO()):
                evidence_pdf.run()
            self.assertEqual([n for n,r,p in seen if r],[1,2])
            self.assertEqual([n for n,r,p in seen if not r],[2,2,2,2,1])
            self.assertIn('施工说明的待验证陈述',[p for n,r,p in seen if n==1 and not r][0])
            self.assertEqual(len(json.loads((out/'engineering/context.json').read_text())['expectations']),1)
