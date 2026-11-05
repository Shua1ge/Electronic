# Fast 光缆路由图候选抽取 v0.3（Ubuntu + NVIDIA CUDA）

这个脚本是 `Electronic` 原工程的**独立实验入口**，不替换 macOS/MLX Audit 流程；支持用本地 Qwen3.5-2B 4-bit 逐切片生成光缆候选，以及基于圆形站点和 PDF 矢量线段的**保守几何核验**。该功能不是通用 CAD 拓扑求解器。

**安全边界：** 所有模型记录默认为待核对。检测到直接矢量线段也只能证明几何连接有线条依据，**不能证明线路旁的芯数/长度属于这条连接**；漏检不能反证线路不存在。当前尚不自动改写模型站名，不自动将任何记录设为「已确认」。

## 安装与全流程运行

```bash
source ~/venvs/electronic-qwen/bin/activate
python -m pip install pymupdf pillow openpyxl numpy opencv-python-headless
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-chi-sim
# 先退出常驻 qwen 聊天进程，释放显存
cd ~/Electronic-fast-v03
python scripts/fast_cable_route.py ~/文档/03.pdf --output ~/fast-cable-03-v03 --node-names qwen
```

默认复用本地缓存模型；若模型尚未缓存，需 Hugging Face 网络连通并添加 `--online`。不要在未确认环境兼容性时重装 PyTorch。`--model-id Qwen/Qwen3.5-4B` 可切换为 4B 进行另一次实验，需新的输出目录。

## v0.3 站名—圆形节点自动候选绑定

**此版本已直接集成在主入口中，新的 Git Clone 不需要下载旧 ZIP，也不需要再运行 `enable_node_names.py` 打补丁。**

- `--node-names qwen`：六块图纸候选抽取后，复用当前已加载的 Qwen3.5-2B，对每个圆形节点单独高清裁剪、识别邻近站名，结合 OCR 产生多个读法及模糊字提示。
- `--node-names ocr`：相同的节点截图、OCR 与候选匹配，但不额外调用节点 Qwen；可降低耗时。
- `--node-names off`（默认）：保留旧版行为。额外模型调用可能较慢，所以新功能暂不默认开启。

**请优先打开新生成的 `review-with-nodes.html`，而不是仅看 `review.html`。** 前者增加每个圆形节点的裁图、OCR/Qwen 读法、站名匹配建议、实际矢量直线候选检查；后者仍是原始基础候选表。相应新增 `structured-with-nodes.xlsx`、`candidates-with-nodes.json`、`geometry/station_name_candidates.json` 和 `geometry/station_name_crops/`。原始输出不会覆盖。

```bash
xdg-open ~/fast-cable-03-v03/review-with-nodes.html
# 如只想测试节点截图与识别（不重新执行六块图纸 VLM）：
python scripts/station_node_linker.py ~/文档/03.pdf ~/fast-cable-03-v02 --mode qwen
# 无 GPU 端到端入口（仅几何和站名 OCR，不运行 VLM）：
python scripts/fast_cable_route.py ~/文档/03.pdf --output ~/fast-geo-03-v03 --geometry-only --node-names ocr --ocr none
```

“自动匹配成功”只是**尚未签认的名称和几何候选**，不会偷偷把“李家垠”改成“李家垸”，也不会自动认定芯数/长度属于该连接。对于粗黑线、多段折线和遮挡，缺少检测结果仍需复核。

## 先单独检查几何效果（不启动 Qwen）

```bash
python scripts/fast_cable_route.py ~/文档/03.pdf \
  --output ~/fast-geo-03 --geometry-only --ocr none
xdg-open ~/fast-geo-03/geometry/page001_overlay.png
```

几何叠加图中**橙色圈/编号**是程序定位到的圆形站点候选，**绿色线**是从 PDF 矢量层中提取、两端都落在两个不同圆形节点上的**直接直线段**。部分站点、粗黑线路、折线或有标注遮挡的线可能漏检；圆圈的编号不是站名。

- `geometry/geometry.json` 与 `geometry/page001.json`：节点的页面归一化框、路径节点 ID 与矢量线段索引；
- `geometry/page001_overlay.png`：一页几何叠加图（多页图纸每页一张）；
- `geometry/station_anchors.csv`：节点编号、位置、OCR 附近的文字线索、**人工确认站名**。

## 人工站点映射备选方案（不强制）

当不启用 `--node-names` 或打开旧的 `review.html` 时，原来的几何模块不会用 Qwen 读法直接绑定站名，因此会显示 **「端点尚未标注到圆形节点」**。可在新 `review-with-nodes.html` 中查看自动候选绑定；以下人工站点映射用于最终核查，不是每次运行的前置条件。

1. 对照 `geometry/page001_overlay.png`，打开 `geometry/station_anchors.csv`。仅填写 `确认站名` 一列，保留页码和节点 ID，保存为 UTF-8 CSV。字形模糊时留空，不要猜测。
2. 再使用**新的**输出目录重新运行：

```bash
python scripts/fast_cable_route.py ~/文档/03.pdf \
  --output ~/fast-cable-03-anchored \
  --station-map ~/fast-geo-03/geometry/station_anchors.csv
```

这时程序只把操作员填写的站名与模型候选两端匹配：若两端的已标注圆圈之间有 PDF 直接线段，就提示「检测到直连矢量线段」；否则提示「未检出直接线段，需复核」，不会把它当作确认的断线。节点和线段只针对同一页配对；姓名OCR不作为已核验身份。

想在**不重新跑 GPU**的情况下，用旧回复做几何核验，可把 `--responses-dir` 指向上一次 `calls` 文件夹（脚本能读取包含 `raw` 字段的 `page001_tile01.json`）：

```bash
python scripts/fast_cable_route.py ~/文档/03.pdf \
  --output ~/fast-cable-03-check \
  --station-map ~/fast-geo-03/geometry/station_anchors.csv \
  --responses-dir ~/fast-cable-03-v02/calls
```

**注意：** 使用 `--responses-dir` 是回放测试；其中未保存的切片将列为未解决，不会自动补数据。禁止直接覆盖已经核查过的输出目录。

## 输出

- `structured.xlsx`：光缆候选、核对状态、OCR 文字证据、**几何检查、圆形端点 ID、几何路径 ID**。Excel 状态为人工可编辑列，但修改尚不会自动同步回 JSON；
- `review.html`：只读复核页、切片和几何叠加图链接；
- `candidates.json`、`ocr.json`、`calls/*.json`：字段、OCR 行、原始模型回答；
- `geometry/station_anchors.csv`、`geometry/geometry.json`、`geometry/page*_overlay.png`：圆圈候选、直线连线、人工站名映射；
- `tiles/`、`pages/`、`tiles.json`、`manifest.json`、`review.csv`：原图/切片/运行记录与 CSV。

## 当前已知限制

- 当前只检测 **PDF 矢量层中直接连接两个圆形站点的直线段**，不能完整追踪多段折返线、断开的线条、虚线/双线的真实业务含义以及被注释遮挡的路径。纯扫描图纸和不同圆形符号规范可能没有结果；
- 对弯折、分叉、相交的图元采取保守策略：交叉不创设连接；不理解的线条不被自动拼成一条；
- “检测到直连”不验证光缆是否确实在图例中属于本工程、不验证类型、芯数、长度文字与线路之间的归属；
- 没有人工站点映射时，不对模型给出的站名强行绑定圈号；
- 现有 Tesseract 在该样例的轮廓化小字、斜标注上效果欠佳；
- 即使独立几何核验为真，模型仍可能把参数挂错连线；
- 尚未建立人工签字、改写事件及正式表回写流程；测试不能当生产工程事实。

## GPU-free 测试

```bash
python -m unittest discover -s tests -p 'test_fast_cable_route.py' -v
python -m unittest discover -s tests -p 'test_station_node_linker.py' -v
```

该测试使用合成输入，不包含用户真实工程 PDF。实际案例 `03.pdf` 的试跑数据仅在本地留存，不能作为独立准确率基准。