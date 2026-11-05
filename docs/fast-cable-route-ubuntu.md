# 光缆路由图 Fast（Ubuntu / CUDA）

这是独立的光缆路由图候选抽取入口，不替换原有 macOS/MLX 审计流水线。**所有非拒绝记录都标记待核对，不是已确认工程事实。**

## 运行

已在 RTX 4060 Ubuntu 上成功加载 Qwen3.5-2B、Transformers、bitsandbytes 的用户可继续复用现有环境。

```bash
source ~/venvs/electronic-qwen/bin/activate
python -m pip install pymupdf pillow openpyxl
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-chi-sim
# 请先退出正在占用显存的 qwen 聊天进程
python scripts/fast_cable_route.py ~/文档/03.pdf --output ~/fast-cable-03-001
```

首次模型没有缓存时可添加 `--online`，但 Hugging Face 连接必须可用；默认离线。针对 4B 对比时更换 `--model-id Qwen/Qwen3.5-4B`，并使用新的输出目录。

## 输出

- `structured.xlsx`：光缆候选、说明、OCR 证据，状态可人工编辑（待核对/已确认/已纠正/拒绝）
- `review.html`：候选清单、原因和原图切片链接；只读
- `candidates.json`：模型字段、证据 ID、疑点、尚未解析的项目
- `ocr.json`：可见 PDF 文字或 Tesseract OCR 行与原图归一化坐标
- `pages/`、`tiles/`、`tiles.json`：渲染图片、裁剪区域位置
- `calls/`：逐切片原始模型回答和生成记录
- `review.csv`：简化表

运行参数：

```bash
python scripts/fast_cable_route.py --help
```

离线回放模式：事先将模型原始回答分别存入 `page001_tile01.txt` 等文件，指定 `--responses-dir <目录>`；没提供回复的图块会记为未解决，不伪造记录。

## 证据和安全边界

本版做了 JSON 检查、字段类型检查、自环/缺端点拒绝、相同记录去重和 OCR 字面比对。**OCR 命中不等于线路端点、长度归属和拓扑已得到验证。** 对不相同的并行线路候选不合并；错误的站名不会自动修正。模型可能将其他线条的长度放在一条虚构连接上，所以所有记录初始必须人工复核。

`structured.xlsx` 是专用的候选工作表，未实现人工签字确认后自动回写 JSON 或直接导入五类业务表。原仓库的几何分析侧重业务组织图设备框，不可直接宣称已验证光缆路由图的圆形站点连线。下一阶段要加独立圆形节点/路径/线路标签归属核验和人工修改事件账本。

## 测试

```bash
python -m unittest discover -s tests -p 'test_fast_cable_route.py' -v
```
