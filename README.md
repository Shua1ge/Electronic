# 电力通信工程图纸结构化抽取原型

> **Ubuntu / NVIDIA CUDA 光缆路由图 Fast v0.2 实验入口**：运行 `scripts/fast_cable_route.py`，支持圆形站点候选、PDF 直接矢量线段、带编号的几何叠加图和待核对 Excel。详见 [Ubuntu Fast 使用说明](docs/fast-cable-route-ubuntu.md)。本功能不覆盖下文 macOS Audit 主链路，也不自动确认工程事实。

**本地离线 · 证据可追溯 · 候选抽取 + 人工复核**

本仓库是一个面向电力通信工程图纸的本地抽取原型：把 PDF 图纸读成五类业务表的候选行，并为每个候选值保留 OCR 原文、页面坐标和几何依据。它不是无人化交付系统；输出是候选记录，不是已确认的业务事实。

## 1. 定位

电力通信工程的设计资料里，大量信息以图纸形式存在：业务通道组织图、光缆路由图、光通信系统配置图、接口连接图等。设计人员需要把这些图读成项目、光缆、光路、保护业务、非保护业务五类业务申请表。

本原型的做法：

- 本地运行，不依赖云端服务；
- 先把图纸变成可定位的 OCR 行和几何结构；
- 再让本地视觉语言模型按“每一条候选连线”抽取字段；
- 每个字段必须能回到 OCR 原文和页面坐标；
- 没有字面依据的字段不进入业务表，移入“未核实字段”；
- 不确定的几何/分类变成有限次数的复查任务，复查回答不自动覆盖事实。

目标不是“替代设计人员”，而是把重复的机械读取变成“可复核的候选人”。

## 2. 与相关研究的对应关系

相关研究关注电力通信业务智能体在长流程任务中的可信运行：依据是否可用、执行是否落实、异常能否接续、人工确认是否有效。本仓库不实现完整的任务语义契约、逻辑写入单元、效果账本、异常恢复和确认绑定，而是把其中与“图纸读取”直接相关的三条原则做成一个可运行的领域原型：

| 研究中的保障维度 | 本仓库的实现 |
|---|---|
| 过程可追溯 | OCR 行 ID + 归一化 bbox；每条通道的 line / path / 端点框 / 标题 / 行文本；每次模型调用的 prompt、原始回答、耗时、token；每次运行的 PDF / 代码 / 模型哈希 |
| 结果可核验 | 字段级 literal 检查；`withheld_fields` 保留未核实原值；`field_evidence` 记录 OCR 行、检查方式和原文；`coverage.json` 记录哪些连线有记录、哪些没有 |
| 人机协同可控 | 有次数上限的定向复查队列；复查只产出候选判断，`business_facts_modified: false`；字段改写保留 `model_value / value / reason / source_ids` |
| 评价真值独立性 | 运行前冻结源码快照与哈希；`audit/` 不被识别流程导入；原始模型回答、失败记录、被拒绝记录全部保留；不使用文件名/原生文字层作为答案提示 |

本仓库是领域原型，不是项目交付物；它验证的是“证据链能不能落到图纸坐标上”，不是完整的智能体可信运行组件。

## 3. 处理流程

```
PDF
 → 整页渲染 + 自动细节图
 → 本地 OCR（行级文本 + 坐标）
 → 页面分类 / 任务路由
 → 图形几何（设备框、候选连线、图例、端点标题）
 → 逐通道本地 VLM 抽取
 → 端点几何约束
 → 证据校验（literal / withheld）
 → 定向复查队列
 → structured.xlsx + review.html
```

各阶段产物：

- `assets/<page>/ocr.json`：OCR 行（ID、文本、置信度、归一化 bbox）。
- `assets/<page>/channels/geometry.json`：设备框、连线路径、端点标题、行文本、图例证据。
- `channel-calls/<page>/<channel>.json`：逐通道模型原始回答与 prompt。
- `calls/<page>/...`：页面路由与整页抽取的原始回答。
- `pages/<page>.json`：逐页候选、尝试记录、被拒绝记录。
- `observations.json` / `structured.json`：逐页候选与聚合候选。
- `structured.xlsx` / `review.html`：面向复核的输出。

## 4. 可信与可追溯设计

### 4.1 OCR 行是锚点

`evidence_pdf.prepare` 把整页渲染到固定长边，OCR 返回行级文本和 bbox，统一编号为 `T001…`。`ocr.json` 保存每行的原文、置信度和归一化坐标；旋转页额外保留旋转前坐标。

模型调用时，`source_ids` 的 JSON Schema 被约束为本次任务实际提供的 OCR 行枚举，配合约束解码，模型不能编造不存在的行 ID。这保证了引用在结构上合法，但**不保证引用语义正确**。

### 4.2 字段级证据

`evidence_core.validate_records` 对每个字段做证据检查：

- 优先使用字段级 `citations`；没有就在 `source_ids` 里找包含该值的行；
- `literal_supported` 做归一化后的子串匹配（空白、全半角、常用单位写法）；
- 引用行不支持时，在本次任务的 OCR 行里找唯一字面匹配，补引用并标记为 `unique_context_literal_match_relationship_unverified`；
- 仍不支持时，字段进入 `withheld_fields`，不写入业务表。

通过的字段会记录 `field_evidence`：来源 OCR 行、检查方式、原文和 bbox。检查方式区分“字面命中”和“引用关系未验证”，避免把“文字存在”误读成“关系已确认”。

### 4.3 未核实字段

没有字面依据的字段不会静默丢弃，也不会强行填表：原值、引用和原因保存在 `withheld_fields`，并在 `uncertainties` 中说明“未核实字段未进入业务表”。Excel 中有独立的“未核实字段”页。

### 4.4 几何与改写记录

每条候选连线的 `geometry.json` 保存：线段和折返路径、两端设备框、端点标题、行文本、图例证据、类别、符号。`geometry.png` 是框/线/图例的叠加图，`coverage.json` 记录哪些连线有记录、哪些没有。

字段被程序改写时，`geometry_field_changes` 记录 `model_value / value / reason / source_ids`。原始模型回答不会被确定性整理覆盖。

### 4.5 复查队列

`project_context.build_graph` 根据几何和字段状态生成定向复查任务：端点一致、缺端点、图例与业务类别、双线身份、未绑定连线、数量核对。

`review_evidence.py` 按任务类型优先级取前 `--limit` 条，带裁剪图和局部 OCR 调用本地模型复读；结果保存原始回答和“下一步需要的证据”，明确 `business_facts_modified: false`，不自动回填业务字段。

### 4.6 运行级不可变性

- `run.json` 的 `signature` 包含 PDF 哈希、模型 revision、输出预算、流水线源码哈希和采样参数；代码/输入变化必须使用新的输出目录。
- 批量验证前把源码快照到 `frozen-source/` 并逐文件哈希；运行中代码变化会报错。
- 路由修正、通道修正、字段归一化前都保留 `before-*` 快照。
- 每次模型调用的 prompt、原始回答、耗时、token 数、是否撞上限都落盘。
- `audit/` 不被识别流程导入，审计答案不回填。

## 5. 当前状态与限制

- 本地 4B 视觉语言模型，4bit，未微调；当前版本先用提示词、JSON 约束和几何约束验证证据链，再评估模型侧投入。
- 输出是候选记录，不是可无人复核交付的业务表。
- 小样本回归只能说明流程可执行，不代表泛化准确率；仓库不附带任何验证数据或准确率数字。
- 已知失败模式：浅灰线漏检、OCR 行跨框合并、图例缺失或误读、端点标题绑定错误、证据校验只查字面不查指代。
- 上述失败模式在 `docs/architecture-v2.md` 中有更完整的边界说明。

## 6. 仓库结构

```
scripts/    核心流程、几何、校验、复查、导出，以及开发期审计/报告脚本
config/     业务字段、工程本体、模型配置、离线沙箱
docs/       架构与本体说明
tests/      单元测试
```

不包含：`data/`（工程图纸）、`outputs/`（运行结果）、`models/`（模型权重）、`tools/`（本地工具）。这些目录已在 `.gitignore` 中排除。

为保证可公开性，仓库还省略了与具体工程样例绑定的示例构建、模型审计和报告脚本；核心流程、几何、校验、复查、导出均保留。

## 7. 运行环境

完整流程面向 macOS Apple Silicon：

- Python 3.13 + uv
- Apple Vision OCR（`tools/bin/vision-ocr`，由 `scripts/vision_ocr.swift` 编译）
- MLX / MLX-VLM
- `sandbox-exec` 离线运行包装
- 可选：ODA File Converter（DWG 转换）

纯 Python 的校验、几何和本体测试可以在其他平台阅读和部分运行，但整条流水线依赖上述 macOS 组件。

## 8. 快速开始

```sh
make setup
make download
make ocr-tool
scripts/local-python scripts/run_document.py /path/to/drawing.pdf --output outputs/run-001
```

`--review-limit` 控制复查次数，`0` 表示只生成队列：

```sh
scripts/local-python scripts/run_document.py /path/to/drawing.pdf --output outputs/run-002 --review-limit 0
```

测试：

```sh
make test
```

## 9. 数据与合规

本仓库不包含任何工程图纸、项目名称、站名、设计院名称、模型权重或运行输出。使用者需自行准备输入数据，并遵守所在单位的数据管理和保密要求。

## 10. 使用与许可

本仓库用于技术交流与原型验证。如需商用或二次开发，请联系作者确认许可与合规要求。
