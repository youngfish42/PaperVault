# PaperVault 技术细节

## 1. 技术架构总览

PaperVault 采用**前后端分离**架构：

- **后端**：Python Flask 提供 REST API，启动时加载本地 JSON 缓存，所有检索均在内存中完成，不依赖数据库。
- **前端**：Vue 3 + Vite + TypeScript 构建的单页应用（SPA），打包后输出为静态文件，由 Flask 直接托管。
- **数据层**：`cache/cache.jsonl.gz` 是原始论文目录缓存（JSON Lines + gzip），**权威副本托管于 Hugging Face Dataset**（由 `PAPERVAULT_HF_REPO_ID` 指定），本地副本由 `data_artifacts.ensure_cache_local()` 在每个入口启动时同步拉取，并通过 `parent_commit` 乐观锁回写；`conf/*.json` 定义需要采集的会议列表。

```
conf/*.json  ──►  collector.py  ──►  cache/cache.jsonl.gz
                                         ▲
                                         │
                                    app.py (Flask)
                                         │
                              ┌──────────┴──────────┐
                              │   /api/search       │
                              │   /api/get_guess... │
                              └──────────┬──────────┘
                                         │
                                    web-vue (Vue 3)
```

---

## 2. 后端设计

### 2.1 主要文件

| 文件 | 职责 |
|------|------|
| `app.py` | Flask 服务入口，加载缓存、暴露 API |
| `collector.py` | 多源论文采集器 |
| `maintain.py` | README 会议列表自动更新工具 |
| `data_artifacts.py` | 同步 `cache/cache.jsonl.gz` 至 Hugging Face Dataset（含 `parent_commit` 乐观锁与重试） |

### 2.2 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 返回前端构建产物 `static/index.html` |
| `/api/search` | GET / POST | 论文检索接口 |
| `/api/get_guess_you_like` | GET / POST | 基于 GPT-3.5 的关键词推荐接口 |

#### `/api/search` 请求参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 检索词。支持特殊值 `#`（返回全部）和 `findall`（单会议全部返回） |
| `year` | int | 起始年份，仅返回该年份之后的论文 |
| `sp_year` | int | 指定年份，精确匹配 |
| `sp_author` | string | 指定作者，精确/模糊匹配 |
| `confs` | string | 会议列表，逗号分隔，如 `ACL,EMNLP,CVPR` |
| `searchtype` | string | `title`（按标题检索）或 `author`（按作者检索） |

**检索逻辑**：
- 先将查询词统一小写，去除多余空格与连字符；
- 在 `title_format`（已归一化的小写标题）中进行子串匹配；
- 支持按会议、年份、作者多重过滤；
- 默认最多返回 **5000** 条结果。

#### `/api/get_guess_you_like` 请求参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 用户输入的检索词 |

该接口调用 OpenAI GPT-3.5-turbo，要求模型返回与输入词相关的 Top-10 论文关键词，供用户扩展检索。

### 2.3 缓存加载机制

`app.py` 在模块导入时即执行 `load_data()`，将 `cache/cache.jsonl.gz` 流式读入内存，按会议和年份组织为嵌套字典 `cache_data`。此后所有搜索均在内存中进行，无磁盘 I/O。

`cache/cache.jsonl.gz` 被重新生成或更新后，`data_artifacts.py` 会在配置 Hugging Face 环境变量时将其上传到对应的 Dataset 仓库。Hugging Face 会自动将 JSON Lines 数据集转换为 Parquet 视图，因此本仓库不再在本地生成或维护 Parquet 文件。

---

## 3. 前端设计

### 3.1 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Vue | 3.x | 渐进式框架 |
| Vite | 4.x | 构建工具与开发服务器 |
| TypeScript | ~4.7 | 类型安全 |
| Element Plus | 2.2.x | UI 组件库 |
| Axios | 1.3.x | HTTP 客户端 |
| @vueuse/core | 9.x | 暗黑模式等组合式工具 |

### 3.2 组件结构

```
src/
├── api/paper.ts              # 封装 /search 和 /get_guess_you_like 请求
├── views/HomeView.vue        # 主搜索页：搜索栏、结果区、侧边栏
├── components/
│   ├── SearchResultList.vue  # 结果分页展示、排序、CSV/TXT 导出
│   ├── ConfsTree.vue         # 会议-年份树形筛选
│   ├── GuessYourLike.vue     # AI 推荐关键词面板
│   └── AdvancedSettingDlg.vue # 高级筛选弹窗（年份、作者、会议）
└── utils/
    ├── axios.ts              # Axios 实例与代理配置
    └── file.ts               # 文件导出工具
```

### 3.3 代理配置

开发模式下，Vite 将 `/api` 请求代理到后端服务（通过 `VUE_APP_BASE_URL` 环境变量配置），实现前后端联调。生产环境则由 Flask 统一托管静态资源与 API。

---

## 4. 数据采集流程

### 4.1 数据源

`collector.py` 支持从以下 5 类来源采集论文元数据：

| 来源 | 会议示例 | 协议 | 解析方式 |
|------|----------|------|----------|
| ACL Anthology | ACL, EMNLP, NAACL, COLING | HTML | BeautifulSoup |
| OpenReview | ICLR, NeurIPS | JSON API | `requests.get().json()` |
| OpenAccess.thecvf | CVPR, ICCV, WACV | HTML | BeautifulSoup |
| NeurIPS Proceedings | NeurIPS, MLSys | HTML | BeautifulSoup |
| DBLP | AAAI, ICML, KDD, WWW 等 30+ 会议 | HTML | BeautifulSoup |

### 4.2 增量更新策略

`collect()` 函数支持增量采集：
- 若 `cache_file` 存在且 `force=False`，则先读取已有缓存；
- 遍历配置列表时，跳过已存在于缓存中的会议；
- 仅对新会议发起网络请求，合并后返回完整结果。

```python
cache_res = json.load(open(cache_file, "r"))
cache_conf = [name for name in cache_res.keys()]
# 后续遍历中若 name in cache_conf 则跳过
```

### 4.3 代码链接匹配

`add_code_links()` 从 [Top-AI-Conferences-Paper-with-Code](https://github.com/MLNLP-World/Top-AI-Conferences-Paper-with-Code) 获取各会议代码链接列表，按论文标题精确匹配（忽略大小写与末尾句号），将代码 URL 回填到 `paper_code` 字段。

### 4.4 引用数与摘要

- **引用数**：原计划通过 Semantic Scholar API 获取，因请求限制当前已注释关闭；
- **摘要**：ACL Anthology、thecvf、NeurIPS 等来源可直接解析；DBLP 来源因网站结构复杂及反爬限制，目前默认留空。

---

## 5. 数据格式说明

### 5.1 会议配置格式 (`conf/*.json`)

```json
[
    {
        "name": "ACL2023",
        "url": "https://aclanthology.org/events/acl-2023/",
        "tag": "/2023.acl-long."
    }
]
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 会议名称 + 年份，如 `ICML2022` |
| `url` | 是 | 该会议在对应数据源的列表页 URL |
| `tag` | 部分需要 | ACL Anthology 等需要额外路径标识，DBLP/OpenReview 等不需要 |

### 5.2 缓存格式 (`cache/cache.jsonl.gz`)

每行为一篇论文的 JSON 对象，额外包含 `conf` 字段标识所属会议：

```jsonl
{"conf": "ACL2023", "paper_name": "Paper Title", "paper_url": "https://...", "paper_authors": ["Author A", "Author B"], "paper_abstract": "Abstract text...", "paper_code": "https://github.com/..."}
{"conf": "ACL2023", "paper_name": "Another Title", ...}
{"conf": "CVPR2023", "paper_name": "...", ...}
```

`paper_code` 默认值为 `#`，表示暂无代码链接。

> 仓库的权威数据集托管于 Hugging Face，Hugging Face 会自动为该 JSON Lines 数据集生成 Parquet 视图，便于通过 `pandas.read_parquet` 等工具直接消费；本仓库不再单独维护 Parquet 产物。

---

## 6. 环境变量与部署要点

### 6.1 必需环境变量

| 变量 | 用途 |
|------|------|
| `OPENAI_API_KEY` | GPT-3.5 关键词推荐功能 |
| `OPENAI_API_BASE` | OpenAI API 代理地址（可选） |
| `HF_TOKEN` | Hugging Face 写入 token，用于上传数据产物 |
| `PAPERVAULT_HF_REPO_ID` | Hugging Face Dataset 仓库 ID，如 `youngfish42/PaperVault`；未设置时跳过上传 |
| `PAPERVAULT_HF_REPO_TYPE` | Hugging Face 仓库类型，默认 `dataset` |

### 6.2 生产构建流程

```bash
# 1. 构建前端
cd web-vue && npm install && npm run build
# 产物输出到 ../static

# 2. 启动后端
python app.py
```

### 6.3 缓存存储（Hugging Face，已替代 Git LFS）

自迁移完成后，`cache/cache.jsonl.gz` 不再由 Git 或 Git LFS 跟踪，请勿尝试 `git lfs pull`。本地副本由 `data_artifacts.ensure_cache_local()` 自动从 Hugging Face Dataset 拉取，回写时使用 `parent_commit` 乐观锁；并发写入冲突会自动重新基线并重试（受 `PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS` / `PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF` 控制）。

最少配置：

```bash
export HF_TOKEN=hf_xxx
export PAPERVAULT_HF_REPO_ID=<your-namespace>/<dataset-repo>
# 可选：完全离线模式，跳过任何 HF 拉取
# export PAPERVAULT_OFFLINE=1
```

详细机制与一次性迁移步骤见 `AGENTS.md` 的 "Cache Storage (Hugging Face)" 与 "One-time migration from Git LFS" 章节。

### 6.4 工作流并发控制

所有会修改 `cache.jsonl.gz` 的 GitHub Actions 工作流（`collect_papers.yml`、`backfill_abstracts.yml`、`update_readme.yml`）共用同一个 concurrency group `papervault-cache`，且 `cancel-in-progress: false`，从而保证它们串行执行、不会互相覆盖。即便如此，本地手工运行 + 自动工作流仍可能并发，因此上传层额外使用 `parent_commit` 乐观锁兜底。

---

## 7. CI/CD 工作流

| 工作流 | 触发条件 | 行为 |
|--------|----------|------|
| `discover_and_update.yml` | 每日定时 / 手动触发 | 自动发现新会议配置并创建 PR |
| `update_readme.yml` | 手动触发 | 运行 `maintain.py force`，更新 README 会议列表 |
