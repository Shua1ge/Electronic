# 文档浏览器标注与 Qwen3.5-9B 评测

本入口以一份 PDF 中的区域为任务单位，分别处理文字、表格和图纸。先人工框定区域和任务，再评价冻结模型的识别结果。标注页不加载模型答案；推理不读取人工真值。现有 macOS Audit、Fast 与图纸复核入口继续保留。

当前覆盖区域内的原文转录、表格结构与单元格读取、图纸对象及连接识别，以及人工预定义判断项。区域位置、任务类型和适用上下文由人工提供，因此结果不能解释为全自动整篇文档理解或自动版面分析的成绩。跨区域解释、同一实体、冲突和适用关系可以记录，第一版不直接给这些关系计算准确率；需要评测时可先定义明确的问题，并提供相关上下文。

## 1. 新电脑安装与检查

Ubuntu 22.04 的 Python 3.10 可用。若已有能运行 Qwen 的环境，可以在该环境中安装依赖；需要隔离时创建新环境。不要运行本仓库面向 macOS/MLX 的 `uv sync` 或 `pip install -e .`。

```bash
git clone --branch feature/qwen35-9b-document-eval --single-branch \
  https://github.com/Shua1ge/Electronic.git "$HOME/Electronic-document-eval"
cd "$HOME/Electronic-document-eval"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# 仅标注、评分、回放，或使用已有本地模型服务器：
python -m pip install -r requirements-annotation.txt
```

直接用 Transformers 加载原始 Qwen 权重时，再安装 CUDA 版 PyTorch 和完整依赖。已有可用 CUDA 版 PyTorch 时不必重装。下面是官方提供的 CUDA 12.6 wheel 安装组合，不需要为了本工具编译 FlashAttention。

```bash
python -m pip install torch==2.14.0 torchvision==0.29.0 \
  --index-url https://download.pytorch.org/whl/cu126
python -m pip install -r requirements-ubuntu.txt
python -c 'import torch; print(torch.__version__, torch.version.cuda); print("CUDA:", torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no CUDA")'
```

标注服务本身不使用 GPU。可选的文字草稿功能先尝试原生文字层，没有可用文字层时尝试 Tesseract；二者都必须人工校正。只手动录入时可以不安装 Tesseract。

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim
```

加载方式参考 [Qwen 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)、[Transformers bitsandbytes 文档](https://huggingface.co/docs/transformers/en/quantization/bitsandbytes) 和 [PyTorch 官方 wheel 安装说明](https://pytorch.org/get-started/previous-versions/)。2080 Ti 的 Transformers 路径采用 NF4、double quant、FP16 和 SDPA；其他支持原生 BF16 的卡可选用 BF16。实际显存仍取决于图像、上下文和输出长度，11GB 能否完成具体任务需要本机实测。第一次先选小区域。

## 2. 用一份 PDF 建立标注工程

将下面 PDF 路径替换为新电脑上的实际位置。工程目录会保存一份本地源 PDF 和页面缓存；整个工程都应放在忽略目录 `data/` 下。

```bash
python scripts/document_annotation.py init \
  "$HOME/文档/telecom-drawing/data/pdf_samples/U0103.pdf" \
  --output data/annotation/D04 --document-id D04
python scripts/document_annotation.py serve data/annotation/D04 --port 8766
```

在本机浏览器打开 `http://127.0.0.1:8766`。服务只监听本机，启动终端需保持运行；Ctrl+C 停止。再次打开同一工程只运行 `serve`，不要重新 `init`。端口占用时改成其他端口，并使用终端显示的完整地址。

先填写标注人。选择物理 PDF 页码和“新区域类型”，点击“框选区域”后在原图拖出范围。正文、表格和图示可以分别框在同一页；不同子图、图例和局部图应分别建区域。标题只用于人工导航，不进入模型提示词。“上移顺序”调整区域处理顺序。

侧向页面可先点击“向左旋转/向右旋转”。额外旋转按区域记录，并用于模型输入。人工框始终还原到默认 PDF 旋转后的整页坐标；模型仅输出目标输入图内部的对象坐标，由程序按记录的仿射变换还原。裁剪、页面旋转、额外旋转和输入图哈希均保存在运行记录中。滚轮缩放，拖动平移；框错后可“调整区域框”或撤销。

### 文字区域

在右侧填写逐字原文和信息状态。可先点击“提取文字草稿”，程序会标明原生文字层或 OCR 来源，不会因此自动复核。保留数字、单位、标点和原文冲突。区域可小到一个段落，不必一次录入整页。

需要评价信息理解时添加“预定义判断项”。例如问题为“该段说明中新建光缆的芯数是多少？”，人工确认值为数字 `24`。问题会进入模型提示词，答案、原文转录和人工证据不会进入提示词。不要将答案写进问题或上下文适用说明。只做转录时不必添加判断项。

### 表格区域

填写行数、列数和顶部表头行数，再逐格选择、录入原文和信息状态。点击单元格可设置跨行/跨列，或拆分合并格；所有网格位置必须恰好覆盖一次。也可以将 Excel 的制表符文本粘贴填入，再核对合并范围。缩小行列数、粘贴替换、合并或删除会改变内容，可用撤销恢复。

确实空白的格子，文字留空并选择“清晰可判定”；看不清应选择“有内容但看不清”，不能当作空白。粘贴填入后仍需逐格核对。第一轮建议只选一个完整小表，或边界明确的表格片段，并明确该片段的行列及表头范围。

### 图纸区域

先选择区域角色。点击“框选对象”，框住站点符号或设备框本体；名称另填，可指定对象类型和所属设备。不同子图中的同名对象保持独立。

点击“添加连接”，依次点击两个已标注对象，设置关系类型和方向。交叉不自动创建接点。平行线分别添加连接。未描路径时，图上的虚线只是端点示意；可选中连接后点击“描线路径”，沿原图点选转折点，再点击“完成路径”。

连接的类型、芯数、长度、建设状态和速率分别保存原文、确认值、单位、信息状态与证据。长度与速率填写数字及单位，计数的单位可留空。容差按统一到 `m` 或 `Mbps` 后的数值填写，并在看模型预测前固定。建设状态保留图例原意；不把“已有或其他工程待建”简化为“已有”。

“当前字段”选择需要关联的属性，再点击“框选证据”，框住对应文字。连接本身也可单独关联证据。改变端点会清空旧路径，并将原属性重新标为待处理。无法确定的连接放在备注/判断项中等待裁决，不加入可判定真值边。

### 状态、上下文和复核

“尚未标注”表示任务没完成；“未提供”表示人工检查后的判断，二者不能互换。还区分清晰可判定、存在歧义、有内容但看不清、不适用。无法确定的字段保留原文和证据，确认值不导出为已知值。未纳入本轮属性评分的字段可点击“本轮不评此字段”，并在研究记录中说明范围。

证据可以跨页：先选中要关联的记录，点击“框选证据”，再切换页码框选。可以将其他区域记录为适用上下文，并写明适用范围；每个区域最多两块额外上下文。跨区域关联单独记录解释、同一实体、冲突或适用于，不会自动合并实体或消除冲突。

自动保存约有1秒延迟，也可点“保存”；只有显示已保存后才关闭页面。修订快照位于工程的 `history/`。保存失败或多个窗口冲突时先下载当前浏览器草稿，避免直接刷新丢失修改。草稿是应急备份，恢复时将服务停止，保留当前 `project.json`，再用确认后的草稿文件替换它并重启。

区域内所有本轮应标内容检查完整后，点击“复核此区域”。未处理字段会阻止复核。对已复核区域修改内容会重新变成草稿。复核人标识和版本会记录；软件的“已复核”是操作者声明，不等同于完成双人独立复核或领域专家认证。争议区域先留草稿，导出后真值为 `null`，不会伪装成“没有对象/内容”。

## 3. 冻结导出、调用模型与单独评分

浏览器顶部可直接导出，显示本地保存目录并提供下载。也可在另一个终端用下面命令生成路径固定的评测集：

```bash
python scripts/document_annotation.py export data/annotation/D04 \
  --output data/eval/D04-A --condition A
```

`manifest.json` 只含源 PDF 定位与哈希、区域、任务类型、旋转、问题和指定上下文。`gold.json` 保存已复核区域的独立真值，草稿区域为 `null`。`annotation_snapshot.json` 保存完整人工标注和证据，用于复核。请将这三个文件和工程保存在本机，不提交到 GitHub。

默认模型为 `Qwen/Qwen3.5-9B`，只读取本地缓存。也可把 `--model-id` 换成原始 Hugging Face 权重目录。GGUF/AWQ 等预量化文件走下方的本地服务器路径，不会尝试再次 NF4 量化。

```bash
python scripts/document_eval.py run data/eval/D04-A/manifest.json \
  --output outputs/D04-9B-A --model-id Qwen/Qwen3.5-9B
```

模型只加载一次，逐区域推理。默认总输入像素预算为每次调用 `786432`，多图共享；输入上限 `8192` tokens，输出上限 `3072` tokens，关闭 thinking，greedy 解码，seed 为 `17+区域序号`。无自动重试、自动修复 JSON 或自动降分辨率。需要联网下载时显式加 `--online`；实验时建议通过 `--revision` 固定模型提交。采样实现、GPU、软件版本、解析错误、截断及显存情况均单独记录。

已有本地 OpenAI 兼容视觉服务器时，可仅装标注依赖，沿用已部署的9B。必须填服务器真实的模型名和量化方式。服务器应支持图片消息，并由操作者配置好视觉投影、上下文长度和 thinking 行为；客户端不替服务器保证实际量化和确定性。

```bash
python scripts/document_eval.py run data/eval/D04-A/manifest.json \
  --backend openai --api-url http://127.0.0.1:8000/v1 \
  --model-id Qwen/Qwen3.5-9B --server-quantization '填写实际量化方式' \
  --api-extra '{"chat_template_kwargs":{"enable_thinking":false}}' \
  --output outputs/D04-9B-A-server
```

`--api-extra` 按服务器文档选择；不支持该字段的服务器应省略此参数并在服务器端关闭 thinking。API 路径的输入 token 上限需由服务器配置，客户端会记录返回的 token 使用量；不要把它与 Transformers 的客户端 token 检查混为一谈。图片仅发送到显式指定的服务器。

推理完成后再评分。评分从原始回答重新解析，校验运行清单和原始调用哈希，不使用可编辑的预测快照。IoU 阈值沿用推理时固定的值，评分时没有临时调阈值选项。

```bash
python scripts/document_eval.py score outputs/D04-9B-A \
  --gold data/eval/D04-A/gold.json --output outputs/D04-9B-A-scored
```

所有输出必须使用新目录，避免覆盖原始结果。没有已复核真值时推理照常完成，运行目录的识别准确率为 `null/NA`；不要把有效 JSON 比例当作准确率。执行失败返回2，输出格式错误返回3，成功返回0；即使失败，也会尽量写出逐项错误和阶段指标，方便打包排查。

## 4. 输出与指标口径

每次推理分别保存 `calls/` 原始回答、提示词、输入图信息和错误，`predictions/` 结构化预测，`images/` 实际模型输入，以及 `run.json` 的模型/代码/配置/软件记录。对象预测快照使用整页坐标，原始回答中的对象坐标使用目标输入图坐标，两者之间的变换保存在图像记录中。

运行目录和单独评分目录都输出 `metrics.json`、`metrics.csv`、`metrics.txt`。CSV 每行包含分组、指标名和值，可用 Excel 打开。按“文档/条件/任务类型”分别统计，不把文字、表格、图纸揉成一个总分。

文字报告字符错误率 CER 和原文精确匹配率。字符比较采用 Unicode NFC 并去除空白，保留标点、大小写和数字；CER 是 Levenshtein 编辑数除以真值字符数，可能大于1，不能解释为准确率。表格报告行列/表头准确率、单元格位置与合并范围的 P/R/F1，以及已知单元格原文的端到端 CER/精确匹配。第一版不报告 TEDS，也不自动判定业务表格语义正确。

图纸对象以 IoU 做最大匹配数量优先、再最大总 IoU 的一对一空间匹配，默认阈值0.5。名称不参与匹配，只做精确规范化比较；对象类型和所属对象另报条件准确率。连接要求空间匹配后的端点、关系类型、方向及所属目标区域一致，一对一计数；多余重复边为FP。连接指标衡量端点和关系识别，当前不衡量完整折线路径重建质量。

属性分别报告匹配到连接后的条件准确率和计入漏连的端到端准确率。长度、速率进行明确单位换算，不做模糊文字纠错。端点和关系相同的平行边可能无法独立对应参数，因此这些匹配跳过属性评分并单独报告数量，不任意选择最有利的配对。端口等微小对象是否适用默认站点 IoU 阈值，需要另定协议后再评。

预定义判断项报告覆盖率、已回答项错误率、已知项回答覆盖率、未知项明确拒答数和遗漏数。分母来自提前定义且有人工标签的判断项；全部拒答时错误率为 `null`。执行/解析失败仍导致已知文字、单元格或图连接的端到端遗漏，不会被计为正确拒答。未标注区域不评分，并显示未评分区域数。

证据只自动检查引用图像和坐标的结构有效性；引用是否真的支持结论需要人工审查，语义支持率保持 `null`。本版本是提示词加严格输出校验，未启用 token 级受约束解码，也不保证模型不会猜测。对幻觉/诚实边界的研究应结合未知判断项、证据审查和漏检情况解释。

A 只给目标区域；B 增加同页整页；C 再增加人工指定适用的文档区域，必须标为 oracle-context 诊断。各条件使用相同总像素上限，单图实际分辨率会变化，仍要结合记录的实际像素、输入 token、耗时和显存解释收益。C 的人工选择能力不等于自动检索能力。区域选择、图纸真值与方法修改均发生在开发文档上时，不可宣称未见文档泛化。

## 5. 把测试结果发回与离线回放

```bash
python scripts/document_eval.py pack outputs/D04-9B-A \
  --scored-dir outputs/D04-9B-A-scored \
  --output outputs/D04-9B-A-return.zip
```

将生成的返回包发回即可；包含原始输出、预测、运行记录和单独评分指标。默认不包含 PDF 和图片，但包含模型读到的文字与本地路径。需要核查视觉错误时可显式加 `--include-images` 包含实际输入图。尚无人工真值或调用失败时省略 `--scored-dir`，仍可打包原始运行记录。PDF 不会被打包。

```bash
# 不重新加载模型，要求清单、提示词、输入图与原运行一致：
python scripts/document_eval.py run data/eval/D04-A/manifest.json \
  --backend replay --responses-dir outputs/D04-9B-A \
  --output outputs/D04-9B-A-replay

# CPU 单元与端到端测试（包含临时本机HTTP服务）：
python -m unittest discover -s tests -p 'test_document_annotation_eval.py' -v
```

回放的模型信息标为 replay，延迟、token 成本和显存不冒充新推理。修改像素预算、裁剪、旋转、问题或提示词后必须重新推理。源 PDF 哈希变动应新建工程。当前检查哈希用于发现意外修改，不构成防篡改签名。

本功能在无可用 CUDA 的环境中验证了合成真值的三任务评分、回放和本机HTTP接口，并用一份实际 PDF 验证浏览器标注与导出。实际 PDF 的操作测试记录保持草稿，不作为专家真值；Qwen3.5-9B 在2080 Ti上的推理、显存和识别表现需要在目标电脑实测。
