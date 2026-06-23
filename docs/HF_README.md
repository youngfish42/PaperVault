---
license: gpl-3.0
task_categories:
  - text-retrieval
  - text-classification
language:
  - zh
  - en
tags:
  - papers
  - academic
  - bibliography
  - nlp
  - computer-vision
  - machine-learning
pretty_name: PaperVault
size_categories:
  - 100K<n<1M
---

# PaperVault Dataset · 论文元数据库

## 🔎 项目简介 · Overview

PaperVault 是一份**持续自动更新**的统一论文元数据库，覆盖自然语言处理、计算机视觉、机器学习、数据挖掘、数据库、语音、系统、网络、安全、理论计算机科学、人机交互、计算机图形学与多媒体等方向的顶级会议与期刊。PaperVault is a **continuously-updated, unified metadata database** of papers from top-tier conferences and journals across NLP, Computer Vision, Machine Learning, Data Mining, Databases, Speech, Systems, Networking, Security, Theory, HCI, Graphics, and Multimedia.

> 🌐 源仓库 / Source: **[github.com/youngfish42/PaperVault](https://github.com/youngfish42/PaperVault)** — Web UI、REST API、采集流水线、Issues / PRs 全部在那里 · Web UI, REST API, crawling pipelines and issues/PRs all live there.

---

## 🆕 最近更新 · Recent Update

<!-- hf-recent-update-start -->
- 📅 **最近更新 · Last updated**: _等待首次同步 · pending first sync_
- 📊 **数据库规模 · Database size**: _等待首次同步 · pending first sync_
<!-- hf-recent-update-end -->

---

## 📈 数据看板 · Statistics at a Glance

下列 4 张统计图与 `cache.jsonl.gz` 同源同步，反映本数据集的最新状态。The four charts below are generated from the same `cache.jsonl.gz` and always reflect the latest state of this dataset.

<p align="center">
  <img src="pics/stats/stats_overview.svg" alt="Statistics Overview" width="850" />
</p>

<p align="center">
  <img src="pics/stats/papers_by_category.svg" alt="Papers by Research Field" width="850" />
</p>

<p align="center">
  <img src="pics/stats/papers_by_year.svg" alt="Annual Paper Collection Trend" width="850" />
</p>

<p align="center">
  <img src="pics/stats/wordcloud.svg" alt="Publication Series Word Cloud" width="900" />
</p>

---

## 📦 数据集内容 · What's in this dataset

| 路径 Path | 格式 Format | 说明 Description |
|---|---|---|
| `cache/cache.jsonl.gz` | gzip-compressed JSON Lines (UTF-8) | 每行一篇论文 · One paper per line; one JSON object per line |

Hugging Face 会自动为 `cache.jsonl.gz` 生成 Parquet 视图，也可直接用 `datasets.load_dataset(...)` 读取，无需手动解压。Hugging Face also exposes an auto-generated Parquet view, so `datasets.load_dataset(...)` works out of the box.

### 📐 字段 Schema

| Field 字段 | Type 类型 | Notes 说明 |
|---|---|---|
| `paper_name` | string | 论文标题（已归一化）· Normalised paper title |
| `paper_authors` | list[string] | 作者列表，按原始顺序 · Author names in order |
| `paper_url` | string | 论文在原始平台的链接（PDF 或落地页）· Canonical URL on the venue's site (PDF or landing page) |
| `paper_abstract` | string | 摘要；未回填时为空字符串 · Abstract; may be empty when not yet backfilled |
| `paper_code` | string | 从摘要中抽取出的 GitHub 仓库 URL；`"#"` 是「未发现代码链接」的占位符 · GitHub repository URL extracted from the abstract; `"#"` is the sentinel for "no code link discovered" |
| `conf` | string | 会议+年份标识，如 `ACL2024`、`NIPS2023`、`CVPR2025`；去掉末尾四位数字即可得到会议系列。注意 NeurIPS Proceedings 沿用历史命名 `NIPS{year}`。Venue + year identifier (e.g. `ACL2024`, `NIPS2023`, `CVPR2025`). Strip the trailing 4-digit year to recover the venue series. Note that NeurIPS Proceedings entries use the historical name `NIPS{year}`. |

> 缺失字段请按空字符串处理。Treat missing fields as empty strings.

---

## ⬇️ 获取方式 · How to download

下面三种方式任选其一即可，**无需克隆 GitHub 仓库**。Pick any one of the three options below — **no GitHub clone is required**.

#### 方式 A · Option A — `huggingface_hub`（推荐 / recommended）

```python
from huggingface_hub import hf_hub_download
import gzip, json

path = hf_hub_download(
    repo_id="youngfish42/PaperVault",
    filename="cache/cache.jsonl.gz",
    repo_type="dataset",
)

with gzip.open(path, "rt", encoding="utf-8") as f:
    for line in f:
        record = json.loads(line)
        # 在这里处理一条记录 · do something with the record
```

#### 方式 B · Option B — `datasets`

```python
from datasets import load_dataset

ds = load_dataset("youngfish42/PaperVault")
print(ds[next(iter(ds))][0])
```

> 数据集只有一个默认 split（非 ML 训练集），不要传 `split="train"`。Single default split — do **not** pass `split="train"`.

#### 方式 C · Option C — `huggingface-cli` / 直接 HTTPS · Plain HTTPS

```bash
huggingface-cli download youngfish42/PaperVault \
    cache/cache.jsonl.gz --repo-type dataset --local-dir ./data
```

> 💡 文件压缩后约 120 MB（会随数据持续增长），解压后是 GB 级 JSONL 流，请按行流式读取，不要整体载入内存。The file is ~120 MB compressed (and growing) and decompresses to a multi-GB JSONL stream. Stream it line-by-line rather than loading the whole thing into memory.

---

## 🔁 更新节奏 · Update cadence

数据集由三个 GitHub Actions 工作流负责重建并推送到本 Hub 仓库 / The dataset is rebuilt and pushed to this Hub repo by three GitHub Actions workflows：

| 工作流 Workflow | 触发节奏 Schedule | 推送的内容 What it pushes |
|---|---|---|
| `collect_papers` | 每月 15 号 16:00 UTC + 手动触发 · 15th of every month at 16:00 UTC + `workflow_dispatch` | 增量抓取新发现的会议/年份组合 · Incremental crawl of newly-discovered conference/year combinations |
| `backfill_abstracts` | 每月 1 号 00:00 UTC + 手动触发 · 1st of every month at 00:00 UTC + `workflow_dispatch` | 为已有论文回填 `paper_abstract` · Adds `paper_abstract` for papers that were collected without one |
| `update_readme` | 仅手动触发 (`workflow_dispatch`) · Manual only (`workflow_dispatch`) | 默认仅刷新 README 与统计；当输入参数 `mode=force` 时执行全量重建 · Refreshes the README and statistics by default; performs a full rebuild only when invoked with `mode=force` |

每次推送都使用 Hugging Face 的 `parent_commit` 乐观锁机制，避免并发覆盖。Each push uses Hugging Face's `parent_commit` optimistic-lock mechanism to avoid silently overwriting concurrent updates.

---

## 🔗 关联仓库 · Related repository

如果你需要完整的搜索 Web UI（智能搜索 + Web of Science 风格的高级查询 DSL）、REST API（`/api/v1/*`）、抓取 / 合并 / 摘要回填流水线源码、收录会议范围、统计仪表盘、项目截图或贡献指南，请前往 GitHub 项目仓库。If you are looking for the full search Web UI (smart search + Web-of-Science-style advanced query DSL), the REST API (`/api/v1/*`), the crawling / merging / abstract-backfill pipelines source code, conference coverage, statistics dashboards, screenshots or contribution guidelines, please visit the GitHub repository.

👉 **[github.com/youngfish42/PaperVault](https://github.com/youngfish42/PaperVault)**

---

## 📜 许可证 · License

代码以 [GPL-3.0](https://github.com/youngfish42/PaperVault/blob/main/LICENSE) 发布；每条论文记录的著作权仍属于原作者 / 出版方，本数据集仅重新分发公开可获取的元数据与链接。Code is released under [GPL-3.0](https://github.com/youngfish42/PaperVault/blob/main/LICENSE); individual paper records remain the IP of their authors/publishers — this dataset only redistributes publicly available bibliographic metadata and links.
