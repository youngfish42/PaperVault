# PaperVault 后续任务执行指南

> 分支：`backfill-data`
> 本文件记录已完成的任务和待执行的后续操作，供随时恢复工作时参考。

---

## 一、已完成的任务（按提交顺序）

| Commit | 说明 |
|:---|:---|
| `19256b5` | 完成 `cache.jsonl` URL 分布与来源分析报告 (`docs/source_analysis.md`) |
| `2dfbffd` | 完成自动化获取规划文档 (`docs/automation_plan.md`) |
| `84a64ca` | 实现自动发现模块 `discovery/`（ACL/CVF/NeurIPS/MLSys/OpenReview/DBLP） |
| `10e37ef` | 修复 ACL Anthology 采集器适配新版页面结构（events→volume 跳转） |
| `18b6182` | 自动生成 2024/2025 会议配置（CVPR/ICCV/WACV/NeurIPS/MLSys/ACL 等） |
| `30ada73` | 新增 GitHub Actions 自动发现工作流（每月运行） |
| `f7f7933` | 将自动发现工作流改为**每天**运行 |
| `e33006f` | 修复 Discovery 模块连接池问题（每次请求新建 Session） |
| `9ff4ac9` | 实现期刊卷号自动映射（JMLR/VLDB/TIP/TPAMI/TKDE 等 9 种） |
| `477ad16` | 自动生成本科 2023–2025 DBLP 配置（新增 73 条，含期刊） |
| `057feae` | 补充 FL-tracker 参考的 16 个会议 + 8 个期刊到 DBLP 发现模块 |
| `b7de928` | 实现 Abstract 批量回填脚本（Crossref→SS→arXiv→OpenAlex） |
| `8108f99` | 替换废弃的代码链接采集方案为基于 Abstract 的正则扫描 |
| `0e0bb7c` | 自动发现新会议配置（2019–2025，共新增 401 条配置） |
| *(当前分支)* | **修复 abstract 回填流程**：安全写入、进度文件 v2、预检统计、新 Phase 定义 |

---

## 二、待执行任务

### 任务 1：生成新会议/期刊配置（Phase 1B） ✅ 已完成

`conf/dblp_conf.json` 已追加新发现的 venue 条目（+236 条，含 16 个新会议 + 8 个新期刊）。

---

### 任务 1.5：自动收集论文条目（增量收集） ✅ 已完成

工作流位置：`.github/workflows/collect_papers.yml`
脚本入口：`python maintain.py collect`

**触发方式**：
1. **定时触发**：每周一北京时间 03:00（UTC 周日 19:00）自动运行增量收集。
2. **Push 触发**：当 `main` 分支的 `conf/**` 文件被修改时（如 discover PR 合并后）自动触发。
3. **手动触发**：支持 `workflow_dispatch`，可选择 `incremental`（默认）或 `force` 模式。

**行为**：
- 自动检测 `conf/*.json` 中有但 `cache/cache.jsonl.gz` 中没有的会议。
- 只收集缺失的会议，保留已有缓存数据不变。
- 收集完成后自动更新 README 会议列表。
- 通过 PR 提交到 `auto-collect-papers` 分支，人工 review 后合并。

**本地手动执行**：
```bash
source F:/Miniforge3/etc/profile.d/conda.sh && conda activate llm
python maintain.py collect
```

---

### 任务 2：Abstract 批量回填（按 conf 粒度执行）

当前 `cache/cache.jsonl.gz` 中共有约 **63,000+** 条论文缺少 abstract。
脚本位置：`scripts/fetch_abstracts.py`

**执行原则**：以单个 `conf`（会议+年份，如 `AAAI2020`）为最小单元，**逐个处理**，优先选择 DOI 比例高的 conf。

#### 执行前必读：预检与备份

```bash
source F:/Miniforge3/etc/profile.d/conda.sh && conda activate llm

# 1. 自动备份当前 cache（脚本运行时也会自动做原子写入，但手动备份更保险）
cp cache/cache.jsonl.gz "cache/cache.jsonl.gz.bak.$(date +%Y%m%d_%H%M%S)"

# 2. 查看待处理 conf 列表（按优先级排序，DOI 比例高的在前）
python scripts/fetch_abstracts.py --list
```

运行任何命令时，脚本会自动输出 **Preflight Check**，包含：
- 总论文数 / 已有 abstract 数 / 缺 abstract 数
- 按 URL 类型统计（DOI vs 非 DOI）
- 按年份、会议统计缺 abstract 分布

**进度查看**：随时打开 `docs/abstract_backfill_progress.md` 查看各 conf 的完成状态。

#### 方式 1：处理单个 conf（推荐，最可控）

```bash
source F:/Miniforge3/etc/profile.d/conda.sh && conda activate llm
python scripts/fetch_abstracts.py --conf ICASSP2022 --chunk-size 500
```
- **目标**：仅处理 `ICASSP2022` 这 1,866 条论文
- **特点**：最保守可控，处理完一个 conf 后立即看到结果，可随时决定是否继续

#### 方式 2：批量处理同会议多年份

```bash
# 处理 AAAI 的所有年份（AAAI2019 + AAAI2020 + AAAI2021 + AAAI2022）
python scripts/fetch_abstracts.py --conf "AAAI*" --chunk-size 500
```
- `*` 为通配符，支持任意模式匹配

#### 方式 3：自动按优先级批量处理（谨慎使用）

```bash
# 自动处理优先级最高的前 5 个 conf
python scripts/fetch_abstracts.py --batch --top 5 --chunk-size 500

# 自动处理全部待处理 conf（耗时极长，不建议一次性执行）
python scripts/fetch_abstracts.py --batch --chunk-size 500
```

对于仓库里的 `backfill_abstracts` 自动工作流，默认每轮只处理 `--top 1`，并且会先恢复
`auto-backfill-abstracts` 分支上的最新进度文件与 cache，再继续选择**新的**最高优先级 conf，
避免在 PR 未合并时反复从 `main` 重新处理同一批会议/期刊。

#### 方式 4：按 Phase 处理（全局模式，仍保留）

如需一次性处理大量论文，仍可使用原有的 Phase 模式：

```bash
# Phase 1: 全部 DOI 论文（~42,000 条，预计 12–20 小时）
python scripts/fetch_abstracts.py --phase 1 --chunk-size 500

# Phase 2: 核心会议非 DOI 论文（~15,000 条）
python scripts/fetch_abstracts.py --phase 2 --chunk-size 500

# Phase 3: 剩余非 DOI 论文（~7,000 条）
python scripts/fetch_abstracts.py --phase 3 --chunk-size 500
```

#### 可选：标题查询 DOI（更慢，谨慎使用）

对非 DOI 论文，可尝试用标题向 Crossref 查询 DOI（限流更严格）：
```bash
python scripts/fetch_abstracts.py --conf "CVPR*" --chunk-size 200 --query-doi-by-title
```

#### 可选：重试失败项 / 断点续传

```bash
# 重试之前标记为 failed 的论文（仅重试 failed，跳过已成功的）
python scripts/fetch_abstracts.py --conf ICASSP2022 --retry-failed --chunk-size 500

# 重试上次中断导致进度已记录但 cache 未更新的论文
python scripts/fetch_abstracts.py --conf ICASSP2022 --retry-partial --chunk-size 500
```

**断点续传**：
- 进度自动保存到 `cache/abstract_backfill_progress.json`（v2 格式，记录 success/failed 状态）
- 默认自动跳过已成功（`status == "success"`）的论文
- 失败项默认跳过，可用 `--retry-failed` 仅重试失败项
- 可随时中断（Ctrl+C），下次运行自动恢复；若担心中断导致 cache 不一致，可用 `--retry-partial`

**Cache 安全写入**：
- 每 chunk 先写入 `cache/cache.jsonl.gz.tmp`，再通过原子重命名替换原文件
- 即使进程崩溃，最多丢失当前 chunk，不会损坏整个 cache

**环境建议**：
```bash
# 设置联系邮箱（进入 Crossref polite pool，降低被限流概率）
export CONTACT_EMAIL=your_email@example.com
```

---

### 任务 3：代码链接扫描

脚本位置：`scripts/fetch_code_links.py`

**执行命令**：
```bash
source F:/Miniforge3/etc/profile.d/conda.sh && conda activate llm
python scripts/fetch_code_links.py --year all
```

**执行时机**：建议在 **Phase 1/2/3 abstract 回填完成后**运行，这样新补充的 abstract 中的 GitHub 链接也能被扫描到。

**特点**：
- 纯正则匹配，无网络请求，处理全部 9 万条论文仅需数分钟
- 保留已有非 `#` 的 `paper_code` 不变
- 如果后续补充了更多 abstract，可重新运行：
  ```bash
  python scripts/fetch_code_links.py --year all --retry-failed
  ```

---

### 任务 4：提交 cache 更新

`cache/cache.jsonl.gz` **已不再由 Git LFS 管理**，权威副本托管于 Hugging Face Dataset。脚本（`scripts/fetch_*`）在写入本地副本后会自动调用 `data_artifacts.sync_cache_artifacts()` 将其推送回 HF（带 `parent_commit` 乐观锁与重试）。本地 git 操作中无需也不应该再 `git add cache/cache.jsonl.gz`——它已经在 `.gitignore` 中。

只需提交进度元数据即可：

```bash
git add cache/abstract_backfill_progress.json docs/abstract_backfill_progress.md
git commit -m "chore: abstract backfill phase X completed"
```

**注意**：
- 确认环境变量 `HF_TOKEN` / `PAPERVAULT_HF_REPO_ID` 已配置，否则脚本只会修改本地副本而无法同步到权威源
- 如果同时有 GitHub Actions 在跑（如 `backfill_abstracts.yml`），不要并发执行同一脚本；工作流之间已通过 `papervault-cache` 并发组互斥，但本地手工运行不在该组内
- 离线/无 HF 凭据时可设 `PAPERVAULT_OFFLINE=1` 跳过远端同步（仅写本地副本，事后再手工 `python -c "from data_artifacts import sync_cache_artifacts; sync_cache_artifacts(commit_message='...')"` 上传）

---

## 三、快速参考卡片

```bash
# 激活环境
source F:/Miniforge3/etc/profile.d/conda.sh && conda activate llm

# 生成新配置（新增 venue）
python -m discovery.generate_conf --start-year 2019 --end-year 2025

# Abstract 回填（三阶段，保守策略）
python scripts/fetch_abstracts.py --phase 1 --chunk-size 500   # DOI 论文，12-20h
python scripts/fetch_abstracts.py --phase 2 --chunk-size 500   # 核心会议非 DOI，8-15h
python scripts/fetch_abstracts.py --phase 3 --chunk-size 500   # 剩余非 DOI，4-8h

# 代码链接扫描（快）
python scripts/fetch_code_links.py --year all

# 增量收集论文（只收集 conf 中有但 cache 中没有的会议）
python maintain.py collect

# 全量重建缓存（谨慎使用，耗时极长）
python maintain.py force

# 仅更新 README 会议列表
python maintain.py

# 验证配置 JSON 有效性
python -c "import json; [json.load(open(f)) for f in ['conf/acl_conf.json','conf/dblp_conf.json','conf/iclr_conf.json','conf/nips_conf.json','conf/thecvf_conf.json']]"
```

---

## 四、风险提醒

1. **Abstract 回填耗时长**：63,906 条空 abstract 全部处理完预计需要 **20–40 小时**的墙钟时间。请利用断点续传机制分多次执行。
2. **API 限流**：Crossref / Semantic Scholar / OpenAlex 均有可能返回 429。脚本已内置指数退避（遇到 429 自动等待），请勿频繁重启脚本。
3. **Cache 文件大**：`cache.jsonl.gz` 约数百 MB，Hugging Face 上传可能需要较长时间；并发写入会触发 `parent_commit` 乐观锁重试，请耐心等待脚本输出 `[+] Uploaded to Hugging Face` 后再退出。
4. **Cache 写入安全**：脚本已改为原子重命名写入，但执行前仍建议手动备份 `cache/cache.jsonl.gz`。
5. **标题匹配误差**：API 返回的标题与本地标题可能存在细微差异，脚本已记录匹配日志，如遇大批量不匹配请检查日志并调整阈值。
6. **进度文件格式**：进度文件已升级为 v2 格式（记录 success/failed），兼容旧版 `processed_urls` 列表格式。
