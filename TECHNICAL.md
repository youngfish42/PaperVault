# PaperVault 技术细节

## 1. 技术架构总览

PaperVault 采用**前后端分离**架构：

- **后端**：Python Flask 3.x，基于 `papervault.create_app` **应用工厂** 装配；通过 `/api/v1/*` 蓝图对外暴露 RESTful 接口（Pydantic v2 校验 + 统一错误信封 + request-id 日志），启动时一次性加载本地 JSON 缓存，所有检索均在内存中完成，不依赖数据库。
- **前端**：Vue 3.5 + Vite 8 + TypeScript 5 构建的单页应用（SPA），提供「智能搜索」与「高级搜索」两条路由，打包后输出为静态文件，由 Flask 直接托管。检索表达式遵循 **Web of Science 风格的 DSL**（详见 §3.4）。
- **数据层**：`cache/cache.jsonl.gz` 是原始论文目录缓存（JSON Lines + gzip），**权威副本托管于 Hugging Face Dataset**（由 `PAPERVAULT_HF_REPO_ID` 指定），本地副本由 `data_artifacts.ensure_cache_local()` 在每个入口启动时同步拉取，并通过 `parent_commit` 乐观锁回写；`conf/*.json` 定义需要采集的会议列表。

```
conf/*.json  ──►  collector.py  ──►  cache/cache.jsonl.gz
                                         ▲
                                         │
                              papervault.create_app() (Flask)
                                         │
                              ┌──────────┴──────────────┐
                              │   /api/v1/papers        │
                              │   /api/v1/confs         │
                              │   /api/v1/suggest       │
                              │   /api/v1/healthz       │
                              └──────────┬──────────────┘
                                         │
                              web-vue (Vue 3 + WoS DSL)
                              ├─ /#/        智能搜索
                              └─ /#/advanced 高级搜索
```

---

## 2. 后端设计

### 2.1 主要文件 / 模块

| 路径 | 职责 |
|------|------|
| `app.py` | WSGI 入口；调用 `papervault.create_app(get_settings())` 装配应用 |
| `papervault/app.py` | 应用工厂；注册蓝图、错误处理、SPA 历史回退 |
| `papervault/config.py` | `Settings` dataclass，所有运行参数从环境变量读取 |
| `papervault/api/v1/{papers,confs,suggest,health}.py` | 各 v1 接口蓝图 |
| `papervault/services/papers.py` | `PaperRepository` 缓存加载 + `search_papers` 内存检索 |
| `papervault/services/suggest.py` | LLM 关键词推荐（DeepSeek 兼容 / OpenAI） |
| `papervault/schemas.py` | Pydantic v2 请求/响应模型 |
| `papervault/errors.py` / `logging.py` | 统一错误信封、request-id 日志 |
| `collector.py` | 多源论文采集器 |
| `maintain.py` | README 会议列表自动更新工具 |
| `data_artifacts.py` | 同步 `cache/cache.jsonl.gz` 至 Hugging Face Dataset（含 `parent_commit` 乐观锁与重试） |

### 2.2 API 接口（v1）

> 旧版 `/api/search` 与 `/api/get_guess_you_like` 已**移除**，请统一迁移至 `/api/v1/*`（详见 `docs/refactor-plan.md`）。

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 返回前端构建产物 `static/dist/index.html`；其余非 `/api/` 的 GET 404 也回退到 SPA |
| `/api/v1/healthz` | GET | 健康检查，返回 `{status, papers, confs}` |
| `/api/v1/papers` | GET | 论文检索 + 分页 |
| `/api/v1/confs` | GET | 已收录会议列表及各年份篇数 |
| `/api/v1/suggest` | POST | LLM 关键词推荐（默认 DeepSeek） |

#### `/api/v1/papers` 请求参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `q` | string | 自由文本检索词；后端做小写子串匹配（可由前端 DSL 解析后下推） |
| `field` | enum | `title` / `author` / `any`，默认 `title` |
| `conf` | string\|string[] | 会议名，单值/多值（重复 `?conf=A&conf=B` 或 `?conf=A,B` 均可） |
| `since` / `until` | int | 年份区间（闭区间） |
| `author` | string | 作者精确/模糊匹配 |
| `sort` | string | `-year` / `year` / `conf` / `-conf` / `title` / `-title` |
| `page` / `size` | int | 分页参数；`size` 上限由 `PAPERVAULT_MAX_PAGE_SIZE`（默认 200）控制 |

响应：`{ "items": [...], "meta": { "page", "size", "total" } }`，每条 `PaperOut` 包含稳定 `id`、`conf`、`year`、`title`、`url`、`authors`、`abstract`、`code`。

#### `/api/v1/suggest` 请求体

```json
{ "query": "federated learning", "model": "deepseek-chat", "max_keywords": 10 }
```

后端会按 `PAPERVAULT_SUGGEST_PROVIDER` 选择 DeepSeek 兼容接口或 OpenAI；前端在调用前会先用 `queryDsl.ts` 把 WoS 风格语法清洗为纯关键词，再请求 LLM。

### 2.3 缓存加载机制

`papervault/services/papers.py` 中的 `PaperRepository` 在 `create_app(eager_load=True)` 时即流式读入 `cache/cache.jsonl.gz`，按会议/年份组织为内存索引；之后所有搜索均零磁盘 I/O。`/api/v1/healthz`、`/api/v1/confs` 等接口在请求阶段调用 `ensure_loaded()` 进行幂等保护，避免冷启动失败导致首次请求 500。

`cache/cache.jsonl.gz` 被重新生成或更新后，`data_artifacts.py` 会在配置 Hugging Face 环境变量时将其上传到对应的 Dataset 仓库。Hugging Face 会自动将 JSON Lines 数据集转换为 Parquet 视图，因此本仓库不再在本地生成或维护 Parquet 文件。

---

## 3. 前端设计

### 3.1 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Vue | 3.5 | 渐进式框架（Composition API + `<script setup>`） |
| Vite | 8.x | 构建工具与开发服务器，启用 `vite-plugin-compression2` 输出 gzip |
| TypeScript | 5.9 | 类型安全 |
| Element Plus | 2.14 | UI 组件库（`unplugin-vue-components` 自动按需引入） |
| Vue Router | 4.6 | Hash 路由，两条路由：`/`（智能搜索）、`/advanced`（高级搜索） |
| Axios | 1.16 | HTTP 客户端，封装为返回 `Promise<T>` 的类型化 wrapper |
| @vueuse/core | 13.x | 暗黑模式、剪贴板等组合式工具 |

### 3.2 组件结构

```
src/
├── api/paper.ts               # /v1/papers /v1/confs /v1/suggest 类型化请求
├── router/index.ts            # 路由：/、/advanced、/settings
├── views/
│   ├── HomeView.vue           # 智能搜索：统一搜索框、语法速查、结果区
│   ├── AdvancedSearchView.vue # 高级搜索：行式条件构建器 + DSL 预览
│   └── SettingsView.vue       # AI 提供方设置
├── components/
│   ├── SearchResultList.vue   # 结果分页 / 排序 / 研究领域·会议·年份 facet / 摘要折叠 / 导出
│   ├── ConfsTree.vue          # 会议-年份树形筛选
│   └── GuessYourLike.vue      # LLM 关键词推荐面板
├── utils/
│   ├── queryDsl.ts            # WoS 风格 DSL 解析 / 求值 / 拆分 / 构建（含单元测试）
│   ├── fields.ts              # 研究领域归类（与 README 会议分类对齐）
│   ├── i18n.ts                # 轻量级 zh/en 双语模块（localStorage 持久化）
│   ├── axios.ts               # 类型化 Axios 实例
│   └── file.ts                # CSV / TXT 导出
└── icons/element-icons.ts     # Element Plus 图标注册
```

### 3.3 代理配置

开发模式下，Vite 将 `/api` 请求代理到后端服务（通过 `VUE_APP_BASE_URL` 环境变量配置），实现前后端联调。生产环境则由 Flask 统一托管静态资源与 API。

### 3.4 Web of Science 风格检索 DSL

`web-vue/src/utils/queryDsl.ts` 提供完整的 DSL 工具链，由 `HomeView` 与 `AdvancedSearchView` 共同消费：

| 能力 | 说明 |
|------|------|
| 字段标签 | `TS`（主题，默认）、`TI` 标题、`AB` 摘要、`AU` 作者、`SO` 会议、`PY` 年份、`AK` 作者关键词；支持 `=` 或 `:` 连接，大小写不敏感；额外接受 `topic/title/abstract/author/conf/venue/year/keywords` 长别名 |
| 布尔运算 | `AND` / `OR` / `NOT` / 前缀 `-`；相邻 token 默认隐式 AND |
| 邻近匹配 | `NEAR/x`（缺省距离 15），按词级 token 距离判定 |
| 短语 | 双引号包裹的精确短语，evaluator 用大小写不敏感子串匹配 |
| 多值列表 | `SO=ICLR,NeurIPS`，任一匹配 |
| 范围 | `PY=2023-2026` / `PY=2023..2026` |
| 分组 | `( ... )` 改变优先级，字段标签可作用于整个子表达式 |
| CJK 标点 | `normalizeQueryInput` 自动将 `（）：，；。"“ ”` 等全角符号映射为 ASCII，避免中文输入即崩溃 |

优先级：`NEAR` > `NOT` > `AND` > `OR`（与 WoS Core Collection 一致）。

DSL 模块对外导出四个核心函数：

| 函数 | 用途 |
|------|------|
| `parseDsl(text) → AstNode` | 将输入解析为 AST |
| `evaluateDsl(paper, ast) → boolean` | 在 `SearchResultList` 中对已加载结果做客户端再过滤 |
| `splitForBackend(ast) → { q, author, conf, since, until, residual }` | 把顶层 AND 子句中可下推的字段（会议、年份、作者、自由文本）摘出来送给 `/api/v1/papers`，其余复杂结构（OR / NOT / NEAR / 嵌套 / 非默认字段）保留在 `residual` 由客户端二次评估 |
| `buildDsl(rows)` | 高级搜索页的行式条件 → DSL 串（自动加 `TS/TI/AB/AU/SO/PY` 前缀、按需用引号或括号包裹） |

回归测试位于 `web-vue/src/utils/__tests__/queryDsl.test.mjs`，由 CI（`.github/workflows/ci.yml`）的前端任务执行。

### 3.5 双语 i18n、深色模式与领域 facet

- `utils/i18n.ts` 是一个零依赖的轻量 i18n 模块，按 `navigator.language` 默认判定 zh/en，并把用户选择持久化到 `localStorage`；所有界面文案、检索语法速查、提示条均走 `t('key')` 调用。
- 顶部工具栏支持深色 / 浅色切换（基于 `@vueuse/core` 的 `useDark`）。
- `components/SearchResultList.vue` 的 refine bar 支持：结果内关键词二次过滤、仅看含摘要 / 仅看含代码、年份范围、**研究领域 facet**（按 `utils/fields.ts` 与 README 会议分类一致）、按会议筛选等，全部在客户端基于已加载结果进行，无需重新请求后端。

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
| `OPENAI_API_KEY` | OpenAI 兼容的关键词推荐密钥（当 `PAPERVAULT_SUGGEST_PROVIDER=openai` 时必填） |
| `OPENAI_API_BASE` | OpenAI API 代理地址（可选） |
| `PAPERVAULT_SUGGEST_PROVIDER` | 关键词推荐提供方，默认 `deepseek`；可选 `openai` |
| `PAPERVAULT_DEEPSEEK_MODEL` | DeepSeek 模型名，默认 `deepseek-chat` |
| `PAPERVAULT_DEEPSEEK_BASE_URL` | DeepSeek 兼容服务地址，默认 `https://api.deepseek.com` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（`provider=deepseek` 时必填） |
| `PAPERVAULT_OPENAI_MODEL` / `PAPERVAULT_OPENAI_TEMPERATURE` / `PAPERVAULT_OPENAI_MAX_KEYWORDS` | LLM 调用参数 |
| `PAPERVAULT_MAX_PAGE_SIZE` / `PAPERVAULT_DEFAULT_PAGE_SIZE` | `/api/v1/papers` 分页上限（默认 200 / 50） |
| `PAPERVAULT_CORS_ORIGINS` | 允许的跨域来源（逗号分隔），不设置则关闭 CORS |
| `HOST` / `PORT` | Flask 监听地址（默认 `127.0.0.1:5001`） |
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
