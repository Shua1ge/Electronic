# 外部模型预标注 JSON 导入（人工复核前，不是真值）

本功能只做一件事：**把已经在其他地方使用 GLM、Qwen 或其他多模态模型生成的 JSON，导入 Electronic 浏览器标注工程，供人修改、查漏、复核。**

- **不调用任何模型 API**，不需要 API Key，也不向云端上传 PDF；模型怎样调用由使用者自行决定。
- 新建独立的 `[AI草稿]` 区域，**不覆盖已有人工区域**；同一输入 JSON 不允许重复导入。
- 对象名称与属性保留模型候选，但其状态保持 `pending/尚未标注`，包括模型给出的数值。人工逐项核对后在网页改为“清晰可判定”，才能“复核此区域”并作为真值导出。
- 不把模型建议的线条路径或证据框提升成人工描绘的路径/证据；这些需要重新核实。来源模型、文件 SHA256、导入时间记录于工程区域元数据。
- 模型可能**漏掉**站点/连接；人工不仅要改错，还要对照完整原图补齐漏项。论文正式测试集应额外进行抽样独立复核，记录教师模型辅助标注的过程。

## 1. 在现有标注项目中使用

代码位于 `scripts/import_preannotations.py`。不要求新建虚拟环境；标注端本身不用 GPU。

```bash
cd ~/Electronic-document-eval
source ~/venvs/electronic-2080ti/bin/activate

# 先在浏览器点击“保存”，再做预标注导入；否则浏览器可能保留旧版本。
# 把在外部得到的模型结果转换为下述规范，保存为 ~/下载/ai_preannotation.json。
python scripts/import_preannotations.py data/annotation/D03 \
  --input ~/下载/ai_preannotation.json --actor reviewer --dry-run

python scripts/import_preannotations.py data/annotation/D03 \
  --input ~/下载/ai_preannotation.json --actor reviewer
```

然后**刷新本地** `http://127.0.0.1:8766` 页面，左侧会新增 `[AI草稿]` 区域。标注工具不调用模型，导入程序也不启动模型。浏览器若报旧版本冲突，请先备份浏览器草稿，确认本地工程新版本后再刷新。

> 已有 `reviewed` 人工区域不会被覆盖。若希望只看 AI 草稿，可先在新的空标注工程中导入。请勿把重复的人工区域和 AI 区域一起当成两份独立真值。选择用于正式评测的区域后，处理多余或重叠草稿。

## 2. JSON 输入契约

参见 [`examples/external_preannotation_example.json`](../examples/external_preannotation_example.json)。外部 API 的输出只需由你自己的脚本整理成下面的格式；**无需在标注网页里增加特定供应商的 API**。

```json
{
  "schema_version": "electronic-preannotation-v1",
  "model": "外部模型名称及版本",
  "document_id": "D03",
  "regions": [
    {
      "page": 1,
      "bbox": [0.1, 0.1, 0.9, 0.8],
      "type": "diagram",
      "bbox_space": "region",
      "title": "局部光缆路由图",
      "data": {
        "region_role": "main_diagram",
        "objects": [
          {"id": "n1", "name": "示例甲站", "kind": "station", "bbox": [0.1, 0.3, 0.15, 0.37]},
          {"id": "n2", "name": "示例乙站", "kind": "station", "bbox": [0.73, 0.3, 0.78, 0.37]}
        ],
        "connections": [
          {"id": "e1", "source": "n1", "target": "n2", "relation": "cable", "directed": false,
            "attributes": {"cable_type": "OPGW", "cores": 24,
              "length": {"value": 11.3, "unit": "km", "raw": "11.3km"}}}
        ]
      }
    }
  ]
}
```

**坐标规则：** `region.bbox` 为整页 PDF 的 0～1 归一化坐标；`bbox_space: "region"` 表示对象坐标是相对于这个区域的 0～1 坐标，`bbox_space: "page"` 表示对象框也用整页 0～1 坐标。必须显式填写 `bbox_space`，否则拒绝导入，以免框错地方。对象框应仅框符号本身。模型无法可靠定位对象时，请先在外部生成有效定位或人工补框；不要使用虚构坐标。

强烈建议在最外层添加 `"source_pdf_sha256": "对应原 PDF 的64位SHA256"`，导入程序将校验它是否与工程源 PDF 一致；省略时仍会在本地检查源 PDF 自身是否被更改，并将当前 PDF 哈希写入导入记录，但不能证明外部结果确实来自该 PDF。

其他支持的区域：

- `type: "text"`：`data: {"transcript": "模型转写的正文"}`；仍为未确认草稿。
- `type: "table"`：`data: {"rows": 1, "cols": 2, "header_rows": 0, "cells": [{"row":0,"col":0,"text":"名称"},{"row":0,"col":1,"text":"型号"}]}`；从 0 开始计数，可选 `rowspan/colspan`，必须完整覆盖网格。

**不能直接把模型原始聊天自然语言、Markdown 代码块、其他供应商专有响应结构当成上面约定的 JSON 文件**。外部调用脚本应将模型回答解析后转为此格式，保留原始回答供审计。输入中的对象 ID 只供连接引用，导入时生成新的本地 ID；模型提供的图形路径、证据引用不视为人工证据。

## 3. 人工核查要点

打开 `[AI草稿]`，确认：对象位置和文字、漏掉的对象、直接连接真实性、交叉与不连接、回路数、OPGW/芯数/长度的线路归属。模型造出的“炉子沟 → 十堰变”不能留作已确认连接。

所有对象名称状态最初都是“尚未标注”，连接属性也是“尚未标注”（虽然右侧有预填建议），**人工确认后才改成“清晰可判定”**；不清楚时选“存在歧义”或“有内容但看不清”，不要捏造真值。标注区域完成后点击“复核此区域”，然后再导出评测集。对无用字段可选择“本轮不评此字段”。

## 4. 安全与当前限制

- 不会自动把 AI 预标注升级为真值；使用者主动将整区域标为 reviewed 后，程序才会导出为已复核真值。
- 当前接口从单独的 JSON 文件导入，**不包含供应商 API 适配或网页一键上传按钮**；外部 API 可以通过单独脚本调用并生成文件。
- 正式评测需标记预标注模型、标注人员、修改历史，测试集不应与调提示词/几何规则使用的开发集混淆。
- 运行 `python -m unittest discover -s tests -p 'test_import_preannotations.py' -v` 验证格式和导入安全。测试为合成数据；使用真实工程 PDF 的完整操作仍需在目标 Ubuntu 机器上做一次验收。