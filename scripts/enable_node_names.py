#!/usr/bin/env python3
"""Opt-in, reversible text patch for Electronic Fast v0.2; backs up the source."""
from pathlib import Path
import argparse
import shutil
import sys


def patch_source(text: str) -> str:
    if '--node-names' in text:
        raise ValueError('已检测到 --node-names；不重复打补丁')
    arg_anchor = '    return parser\n'
    if text.count(arg_anchor) != 1:
        raise ValueError('不能唯一定位 make_parser 的结尾；请按文档手工插入代码')
    text = text.replace(arg_anchor,
        '    parser.add_argument("--node-names", choices=["off", "ocr", "qwen"], default="off",\n'
        '                        help="站名与圆形节点绑定：off关闭；ocr仅OCR；qwen复用本地视觉模型")\n'
        '    parser.add_argument("--name-max-nodes", type=int, default=40,\n'
        '                        help="最多处理的节点数，防止误检导致大量推理")\n'
        '    return parser\n',1)
    options = [
        '    export_html(out, rows, manifest)\n',
        '    export_html(out, rows, manifest, geometry)\n',
    ]
    anchors = [s for s in options if s in text]
    if len(anchors)!=1:
        raise ValueError('不能唯一定位 export_html 调用；请按文档手工插入代码')
    injection = (
        '    if args.node_names != "off":\n'
        '        if args.geometry == "off":\n'
        '            raise ValueError("--node-names 需要 --geometry auto")\n'
        '        from station_node_linker import run as link_station_names\n'
        '        station_mode = args.node_names if engine is not None else "ocr"\n'
        '        if args.node_names == "qwen" and engine is None:\n'
        '            print("未执行视觉模型推理，站点读名回退为OCR", flush=True)\n'
        '        result_names = link_station_names(pdf, out, mode=station_mode,\n'
        '            max_nodes=args.name_max_nodes, existing_backend=engine)\n'
        '        print("站名节点报告：", result_names["result_html"], flush=True)\n'
    )
    text = text.replace(anchors[0], anchors[0] + injection, 1)
    return text


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('fast_script', nargs='?', type=Path, default=Path('scripts/fast_cable_route.py'))
    args=p.parse_args()
    target=args.fast_script
    src=target.read_text(encoding='utf-8')
    edited=patch_source(src)
    backup=target.with_name(target.name+'.before-node-names.bak')
    if backup.exists():
        raise SystemExit(f'已有备份 {backup}；为避免覆盖请先检查')
    shutil.copy2(target,backup)
    target.write_text(edited,encoding='utf-8')
    import py_compile
    try:
        py_compile.compile(str(target),doraise=True)
    except Exception:
        target.write_text(src,encoding='utf-8')
        raise
    print(f'补丁已应用: {target}\n原文件备份: {backup}\n运行时添加 --node-names qwen')


if __name__=='__main__':
    main()