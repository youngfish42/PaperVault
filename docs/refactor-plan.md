# PaperVault Web 层现代化重构计划

> 起草日期：2026-06-19
> 适用范围：Web 端（Flask 后端 API + Vue 前端 + 工程化），数据采集 / Hugging Face 同步 / CI 调度均**保持现状**，仅在必要处拆分文件。
> 背景：当前 Web 端尚未对外提供服务，允许进行破坏性变更。重构完成后再上线。

## 0. 总目标

1. **后端**：把 `app.py` 拆成"应用工厂 + Blueprint"结构，新增带 schema 校验、分页、统一响应的 `/api/v1/*`；旧 `/api/search`、`/api/get_guess_you_like` 直接下线（无外部用户）。
2. **前端**：升级到与 Vue 3.5 / Vite 8 / TS 5.9 时代匹配的工程结构（Pinia + feature 目录 + i18n 基础 + 严格 TS + 现代 Element Plus 写法），删除硬编码会议列表，全部走后端契约。
3. **工程化**：补 lint / type-check / test 工作流，依赖治理（Python lock / pnpm），把 collector / maintain 巨石拆分预案落纸。

## 1. 阶段划分

### 阶段 0：安全 & 联调修复（小步、零风险，先落地）

后端：
- `app.py` 顶部 `load_dotenv()`，统一从 `.env` 注入。
- 删除 `debug=True`、主机/端口走环境变量；`host` 默认 `127.0.0.1`。
- 修复 `/api/get_guess_you_like` 的 `timecost` 单位 bug（秒×100 当 ms）。
- 裸 `except:` 改成 `except Exception:` 并 `logger.exception(...)`。
- `/api/search` 缺参时返回 400 而非崩溃。

前端：
- `.env.development` 把 `VUE_APP_BASE_URL` 改为绝对 URL `http://127.0.0.1:5001`，dev 代理才有效。
- `vite.config.ts` 的 `outDir` 改成 `../static/dist`，避免 `emptyOutDir` 误清空整个 `static/`。
- `index.html` 去掉 `maximum-scale / user-scalable=no`（违反 a11y）。
- `utils/axios.ts` 拦截器对齐后端真实响应 `{ msg, data }`，删除假想的 `code` 死分支。
- `utils/file.ts` 用 `Blob` + `URL.createObjectURL` 代替 `data:` URI 导出。
- `HomeView.vue` 把 `'@/api/paper.js'` 改回 `'@/api/paper'`。
- `AdvancedSettingDlg.vue` `watch` 内用 `Object.assign(formData, v)` 替代整对象赋值。
- `SearchResultList.vue` `<el-radio label="Year">` 改为 `value="Year"`，兼容 Element Plus 2.6+。

不引入新依赖、不改 API 契约。

### 阶段 1：后端 v1 API 重塑（破坏性，直接弃用旧 API）

目录：

```
papervault/
  __init__.py            # create_app() 工厂
  config.py              # 通过 pydantic-settings 或简单 dataclass 收敛环境变量
  logging.py             # 结构化日志 + request id
  cache_store.py         # 单例 cache 加载/查询/reload
  errors.py              # 统一错误处理
  schemas.py             # pydantic v2 请求/响应模型
  api/
    __init__.py          # Blueprint('api_v1', url_prefix='/api/v1')
    papers.py            # GET /papers, GET /papers/<id>
    confs.py             # GET /confs
    suggest.py           # POST /keywords/suggest
    health.py            # GET /health
  services/
    openai_client.py
app.py                   # 仅保留：from papervault import create_app; app = create_app()
```

新增/变更端点：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/v1/health` | GET | 进程存活 + cache 状态 + commit id |
| `/api/v1/confs` | GET | 返回会议列表（含每个会议可用年份） |
| `/api/v1/papers` | GET | 分页检索：`q`, `conf`（可重复）, `since/until`, `author`, `page`, `size`, `sort` |
| `/api/v1/papers/{id}` | GET | 详情（含 abstract / code） |
| `/api/v1/keywords/suggest` | POST | OpenAI 推荐，body `{query, model?, max_keywords?}` |

统一响应：

```jsonc
// 成功
{ "data": ..., "meta": { "page": 1, "size": 50, "total": 1234 } }
// 失败
{ "error": { "code": "BAD_REQUEST", "message": "...", "details": {...} } }
```

实现要点：
- pydantic v2 校验入参，校验失败统一 400。
- `cache_store` 在 `create_app()` 内显式初始化，禁止模块导入期重 IO。
- OpenAI 走 `response_format={"type":"json_object"}` + 失败兜底（不再 regex 解析）。
- `/api/v1/papers` **服务端分页**，删除 `limit=5000` 一把梭。
- 旧 `/api/search`、`/api/get_guess_you_like` **直接删除**（用户允许）。

### 阶段 2：前端框架重塑（破坏性）

技术栈（保持主框架，补齐生态）：

| 项 | 当前 | 目标 |
|---|---|---|
| Vue / Vite / TS | 3.5 / 8 / 5.9 | 保持 |
| 状态管理 | 无 | Pinia |
| i18n | 无 | vue-i18n（先开骨架，中英文案分离） |
| 路由 | hash 模式 | history 模式，Flask catch-all 兜底 |
| 类型生成 | 手写 `Object` | openapi-typescript（如有 OpenAPI 文档），否则手写 `schemas.ts` |
| ESLint | 8 + `.eslintrc.cjs` | ESLint 9 flat config + `@vue/eslint-config-typescript` 现代版 |
| Prettier | 2 | 3 |
| 测试 | 无 | vitest + @vue/test-utils（最少 smoke） |

目录：

```
web-vue/src/
  app/              # main.ts, App.vue, providers
  shared/           # ui kit / theme / icons / 通用 utils
  features/
    search/         # SearchPage.vue + composables/useSearch.ts + api/searchApi.ts
    suggest/        # AI 关键词
    export/         # CSV/TXT 导出
  entities/
    paper/          # 类型、单条结果卡片
    conf/           # 会议树、useConfs.ts
  pages/            # 顶层页面
  stores/           # pinia
  api/              # axios 实例 + 端点封装
  locales/          # zh-CN, en-US
```

关键替换：
- `reactive({ val: [...] })` 反模式全部改 `ref<T[]>([])` / `shallowRef`。
- 会议列表硬编码两份（`HomeView.vue`、`AdvancedSettingDlg.vue`）→ 启动期 `useConfsStore().fetch()`。
- 主页业务从 326 行的单文件组件抽到 `features/search`。
- `<el-icon icon="Search" />` 字符串图标改成 `<el-icon><Search/></el-icon>`。
- `<el-config-provider :locale :namespace>` 全局挂载。
- `tsconfig.json` 开 `strict: true, noUncheckedIndexedAccess: true`，按文件渐进消灭 `any`。
- 前端只调 `/api/v1/*`，错误响应按 `{ error: {...} }` 处理。

### 阶段 3：工程化 & 巨石拆分

- CI 增 `web-ci.yml`：`pnpm install --frozen-lockfile && pnpm lint && pnpm type-check && pnpm test && pnpm build`。
- CI 增 `python-ci.yml`：`ruff check .`、`pytest`。
- Python 依赖：迁 `pyproject.toml` + `uv.lock`（或 `pip-tools`）。
- 包管理可选切 `pnpm`（不强制）。
- `collector.py` (1010 行) 拆为 `papervault/collectors/{acl,nips,iclr,cvf,dblp}.py`。
- `maintain.py` (1067 行) 拆为 `papervault/maintain/{readme,stats,charts,wordcloud,fonts}.py`。
- 添加 husky + lint-staged + commitlint（可选）。

## 2. 破坏性变更清单（汇总）

- `/api/search`、`/api/get_guess_you_like` 删除，由 `/api/v1/papers`、`/api/v1/keywords/suggest` 取代。
- 响应体格式 `{ msg, data }` → `{ data, meta }` / `{ error }`。
- 前端硬编码会议列表删除；启动时调 `/api/v1/confs`。
- 前端路由由 hash 模式改 history 模式。
- `vite.config.ts` 产物目录由 `static/` 改 `static/dist/`，Flask 静态托管路径同步调整。
- TS `strict: true` 启用后大量隐式 any 编译报错（重构期一次性消化）。
- Python 入口建议改为 `gunicorn "papervault:create_app()"`，`python app.py` 仅作为开发壳。

## 3. 验收基线

| 项 | 阶段 0 | 阶段 1 | 阶段 2 | 阶段 3 |
|---|---|---|---|---|
| 后端可用 | ✅ 兼容旧 API | ✅ 仅 v1 | ✅ | ✅ |
| 前端可用 | ✅ 旧 API 调通 | ⚠️ 需配合 v1 | ✅ 走 v1 | ✅ |
| Lint / Type-check | 手工 | 手工 | 手工 | CI 强制 |
| 测试 | 无 | 关键 service 加 pytest | vitest smoke | CI 强制 |

## 4. 风险与回滚

- 数据层完全不动；任意阶段失败只需 `git revert`。
- 阶段 1 与阶段 2 存在契约耦合：完成顺序固定为 **先后端 v1，再前端切换**。
- 巨石拆分（阶段 3）独立可回滚，不影响 Web 服务。

## 5. 执行顺序（本次开工）

1. 阶段 0 一次性提交（小步可控）。
2. 阶段 1：搭出 `papervault/` 包 + 工厂 + v1 端点 + 删除旧端点。
3. 阶段 2：前端切到 v1 + Pinia + 目录重组 + 删硬编码。
4. 阶段 3：CI / lint / 巨石拆分按需排期。

## 6. 进度记录

### 阶段 0 — 已完成

后端 (`app.py`)
- `load_dotenv()` 启动注入 `.env`
- 默认 `host=127.0.0.1`，`HOST/PORT/FLASK_DEBUG` 走环境变量；不再硬编码 `debug=True/0.0.0.0`
- `/api/get_guess_you_like` 时延单位修正为 ms (`(ed-st)*1000`)
- 裸 `except:` 替换为 `except Exception:` + `logger.exception(...)`
- `/api/search` 入参校验：缺 `confs`、`query`、`sp_year` 解析失败均返回 400 JSON
- 静态托管目录调整为 `static/dist`，与前端构建目录同步

前端 (`web-vue/`)
- `vite.config.ts`：`outDir` 改 `../static/dist`，dev proxy 从 `VITE_DEV_PROXY_TARGET`（默认 `http://127.0.0.1:5001`）注入，避免 `emptyOutDir` 误清空 `static/`
- `.env.development`：新增 `VITE_DEV_PROXY_TARGET=http://127.0.0.1:5001`
- `index.html`：放开缩放（`maximum-scale=1` / `user-scalable=no` 去除），标题改 `PaperVault`
- `utils/axios.ts`：拦截器同时兼容旧 `{msg,data}` 与新 `{error}` 错误格式，状态码失败统一走 `ElMessage.error`
- `utils/file.ts`：导出改用 `Blob + URL.createObjectURL`，去掉 `data:` URI 旧写法，正确转义 CSV
- `views/HomeView.vue`：`'@/api/paper.js'` 修正为 `'@/api/paper'`；首页标题改 `PaperVault`
- `components/AdvancedSettingDlg.vue`：`watch` 内 `formData = v` 反模式改 `Object.assign(formData, v)`；`<el-checkbox>` 改用 `:value`；修正 `<style scioed>` 拼写
- `components/SearchResultList.vue`：删除条目改按对象引用查找下标，修复"按 Conf 排序后绝对索引错位"导致删错条目的 bug；`<el-radio>` 改 `:value`

### 阶段 1 — 已完成

新结构（已落地）：

```
papervault/
  __init__.py
  app.py                 # create_app()
  config.py              # Settings (env + 默认值)
  logging.py             # 结构化日志 + request id
  errors.py              # ApiError + 全局错误处理 -> {error:{code,message,details}}
  schemas.py             # pydantic v2 入参/响应模型
  services/
    papers.py            # PaperRepository + search_papers() (服务端分页/排序)
    suggest.py           # OpenAI 关键词推荐（错误归一）
  api/v1/
    __init__.py
    health.py            # GET /api/v1/healthz
    confs.py             # GET /api/v1/confs (含每会议年份与计数)
    papers.py            # GET /api/v1/papers (q/field/conf*/since/until/author/sort/page/size)
    suggest.py           # POST /api/v1/suggest
app.py                   # 仅 `app = create_app(get_settings())` + `app.run`
```

破坏性变更：
- 旧 `/api/search`、`/api/get_guess_you_like` **直接删除**；前端必须切到 `/api/v1/*`。
- 响应体统一：成功 `{"items":[...], "meta": {page,size,total}}` 或资源对象；失败 `{"error":{"code","message","details?"}}`，HTTP status 与 code 对齐。
- 服务端分页：`/api/v1/papers` 不再返回全量；`size` 上限由 `PAPERVAULT_MAX_PAGE_SIZE` 控制（默认 200）。
- 模块导入期不再做 IO；`PaperRepository.ensure_loaded()` 在 `create_app()` 中 eager 调用，失败降级为懒加载（不阻塞启动）。
- `PAPERVAULT_OFFLINE=1` 时不再访问 Hugging Face，直接用本地 cache（已与 `data_artifacts.ensure_cache_local()` 联动）。
- 新增依赖 `pydantic>=2.6,<3`（已写入 `requirements.txt`）。

冒烟验证（`PAPERVAULT_OFFLINE=1`，本地无 cache）：
- `GET /api/v1/healthz` → 200 `{status:"ok", papers:0, confs:0}`
- `GET /api/v1/confs` → 200 `{items:[], total:0}`
- `GET /api/v1/papers?size=1` → 200 `{items:[], meta:{page:1,size:1,total:0}}`
- `GET /api/v1/papers?since=abc` → 400 `{error:{code:"BAD_REQUEST", details:[…pydantic…]}}`
- `GET /api/v1/papers?size=9999` → 400 `{error:{code:"BAD_REQUEST", details:[{ctx:{le:500}…}]}}`

### 阶段 2 — 已完成（务实版，按用户"不过度设计"裁剪）

实际落地（破坏性变更已上线）：

- `web-vue/src/api/paper.ts` 完全重写，按 v1 契约定义 `PaperItem` / `PageMeta` / `PaperSearchQuery` / `ConfItem` / `SuggestResponse` 等类型，导出 `searchPapers` / `listConfs` / `suggestKeywords` 三个端点封装；axios 参数序列化对齐多值 `conf` 重复参数（`paramsSerializer: { indexes: null }`）。
- `web-vue/src/views/HomeView.vue`：
  - 启动期通过 `listConfs()` 拉取后端会议树，删除硬编码会议列表；
  - 搜索改走分页 `searchPapers()`（默认 `size` 取后端上限），将后端扁平 `items` 通过 `groupByConfYear()` 适配为旧组件期望的 `{conf:{year:[paper]}}` 嵌套结构，避免重写 `ConfsTree` / `SearchResultList`；
  - AI 关键词调 `suggestKeywords()` 并按新响应 `{keywords, timecost_ms}` 渲染。
- `web-vue/src/components/AdvancedSettingDlg.vue`：移除组件内硬编码 `CONFS_LIST`，改通过 `defineProps<{ confs: ConfItem[] }>()` 接收父组件下发；修复 `FORMDATA` 类型在 `defineProps` 之后定义引发的 TS 报错；`<el-checkbox>` 全部由废弃的 `:label` 迁到 `:value`。
- `web-vue/src/components/SearchResultList.vue` & `ConfsTree.vue`：
  - `reactive({ val: [...] })` 反模式全部替换为 `ref<PaperItem[]>([])` / `shallowRef<Tree[]>([])`，模板里所有 `xxx.val.length` / `xxx.val.slice(...)` 同步改成直接读 `xxx.length` / `xxx.slice(...)`，根除 reactivity 边界 bug；
  - 排序 / 删除 / 过滤逻辑以"不可变副本 + indexOf 引用查找"重写，避免按 Conf 排序后绝对索引错位的旧 bug。
- IDE 类型诊断（`HomeView.vue` / `AdvancedSettingDlg.vue` / `SearchResultList.vue` / `ConfsTree.vue` / `api/paper.ts`）全部为零。

按用户要求**未做**的"过度设计"项（已从原计划裁剪，留待真有需求时再开）：

- 不引入 Pinia：当前只有单页面 + 启动期一次性拉取 confs，`ref` 已足够，避免增加一层无收益的状态层。
- 不引入 vue-i18n：UI 全英文且当前无多语言需求；保留未来切换的可能（仅在 docs 中保留骨架建议）。
- 不做 `features/entities/pages` 目录大改：现有 4 个组件 + 2 个 view 体量极小，重组带来的成本远大于收益。
- 不开 `noUncheckedIndexedAccess` 等额外严格项：`@vue/tsconfig` 默认 `strict: true` 已开，进一步收紧会引入大量与本次重构无关的红线。
- 路由继续保持 hash 模式：避免引入 Flask catch-all 路由 + 部署侧配置变更，与"先恢复可用、再谈优化"原则一致。

### 阶段 3 — 进行中（最小可用工程化，不过度设计）

执行原则：**只补"能阻止下次回退"的最少工程化基线**，不引入 pnpm/uv/husky/commitlint 等会增加心智负担又对当前规模收益有限的工具链。

落地项（本阶段范围）：

1. **后端冒烟测试**：在 `tests/` 下新增 `test_api_v1.py`，以 `PAPERVAULT_OFFLINE=1` + 空 cache 启动 app，验证 `/api/v1/healthz`、`/api/v1/confs`、`/api/v1/papers` 的 200 / 400 路径。无需 mock 网络，CI 中 1 秒级跑完。
2. **CI 工作流 `ci.yml`**：
   - Python：`pip install -r requirements.txt && pip install pytest && PAPERVAULT_OFFLINE=1 pytest -q`；可选 `ruff check .`（如果 ruff 已装）。
   - Node：`npm ci && npm run type-check && npm run lint`（在 `web-vue/` 子目录）。
   - 只在 PR 与 `push` 到主分支时触发；与现有 `collect_papers.yml` 等数据流水线互不干扰（无共享 concurrency group）。
3. **巨石拆分预案**：`collector.py` (1010 行) / `maintain.py` (1067 行) 本阶段**不动代码**，仅在 docs 中给出拆分蓝图（见下），因为这两个文件正被多个 GH Actions 直接 `python collector.py` 调用，破坏式重构需要专门的回归窗口。

延后项（明确不做）：

- 不迁 `pyproject.toml` / `uv.lock`：`requirements.txt` 在当前 CI 工作流（GitHub Actions + Python 3.10）下完全够用，没遇到依赖冲突。
- 不切 pnpm：`package-lock.json` 已存在，`npm ci` 在 CI 中已可重复构建。
- 不加 husky / lint-staged / commitlint：单人维护项目，本地约束属过度治理。
- 不加 vitest：前端组件几乎全是表单 + 列表渲染，单元测试 ROI 低；类型检查 + lint 已能覆盖大多数回归。

巨石拆分蓝图（仅记录，不在本阶段执行）：

```
papervault/
  collectors/
    base.py          # 抽象 collector / 进度文件 / 软超时
    acl.py           # ACL Anthology
    nips.py          # NeurIPS + MLSys proceedings
    iclr.py          # OpenReview ICLR / NeurIPS
    cvf.py           # OpenAccess CVF (CVPR/ICCV/WACV)
    dblp.py          # DBLP 30+ 杂项
    __main__.py      # 等价于现 collector.py CLI 入口
  maintain/
    readme.py        # README 表格 / 徽章渲染
    stats.py         # 数值统计
    charts.py        # matplotlib 柱状图 / 饼图
    wordcloud.py     # wordcloud 渲染
    fonts.py         # CJK 字体探测
    __main__.py      # 等价于现 maintain.py CLI
```

拆分动作必须配套：
- 保留旧入口 `python collector.py` / `python maintain.py` 作为 thin shim，转调 `python -m papervault.collectors` / `python -m papervault.maintain`，避免 GH Actions 一次性大改。
- 在改造 PR 中跑一次完整 `discover_and_update.yml` + `collect_papers.yml` 干跑（`workflow_dispatch`）验证。

### 阶段 3 — 已落地内容

- 新增 `tests/test_api_v1.py`（6 个用例），全部 `PAPERVAULT_OFFLINE=1` 跑通：
  - `/api/v1/healthz` 200 + `{status,papers,confs}`
  - `/api/v1/confs` 200 + 空列表
  - `/api/v1/papers?size=1` 200 + `{items:[], meta:{page,size,total}}`
  - `/api/v1/papers?since=abc` 400 + `error.code=BAD_REQUEST`
  - `/api/v1/papers?size=99999` 400（被 pydantic `le=500` 拦下）
  - `/api/v1/papers?conf=ACL&conf=EMNLP` 200（验证多值 `conf` 重复参数）
- 新增 `pytest.ini`，固定 `testpaths=tests`，并屏蔽与本仓库无关的 DeprecationWarning。
- `web-vue/package.json` 新增 `lint:check` 脚本（`--max-warnings 0` 不写盘），供 CI 使用；保留原 `lint`（带 `--fix`）给本地开发。
- 新增 `.github/workflows/ci.yml`：
  - Python 3.10：安装 `requirements.txt` + `pytest`，跑 `pytest -q`（`PAPERVAULT_OFFLINE=1`）。
  - Node 20.19.0：`npm ci` → `npm run type-check` → `npm run lint:check`。
  - 仅 push / PR / 手动触发；与数据流水线（`collect_papers.yml` 等）**无共享 concurrency**，相互不阻塞。
- 巨石拆分本阶段**未执行代码改动**，仅以蓝图形式留档（见上一节），等后续有专门回归窗口时再启动。

### 阶段 3 — 增量加固（CI 路径过滤 + 真数据测试）

在最小工程化基线上又补了两件"防回退"的事，目的是让 CI 既不被自动 PR 滥触发，又能真正测到 v1 API 的业务行为：

1. **`ci.yml` 加 paths 白名单**：`push` / `pull_request` 触发仅在以下路径变更时生效，避免 `update_readme.yml` / `collect_papers.yml` / `backfill_abstracts.yml` 等自动 PR（改 `README.md` / `cache/*.json` / `pics/**` / `conf/**`）持续消耗 Action 配额：
   - `papervault/**`、`tests/**`、`web-vue/**`
   - `app.py`、`data_artifacts.py`、`requirements.txt`、`pytest.ini`
   - `.github/workflows/ci.yml` 自身（保证下次改 CI 配置时能触发自检）
   - `workflow_dispatch` 不受 paths 约束，调试时可强跑。
   - 注意：未来若把 CI 设为 branch protection 的 Required check，需补一个无条件跑的 noop job 兜底，否则被 paths 过滤跳过的 PR 会因"required status missing"而无法合并。
2. **真数据 fixture + service 层测试**：
   - `tests/conftest.py` 在 `tmp_path` 下用 `gzip.open(..., "wt")` 程序化生成 7 条合成论文 + 1 条"无年份" conf key 的负样本，写出与生产同格式的 `cache.jsonl.gz`。**不向仓库提交二进制 fixture**，避免版权风险与不可读 diff，且永远与 `collector.save_cache` 的写出格式保持一致。
   - `tests/test_papers_search.py`（11 个用例）：覆盖 `/api/v1/confs` 年份聚合 + 跳过无年份会议、`/api/v1/papers` 的 title / any / 单词作者 / 多词作者 / 多值 conf 过滤 / since-until 边界 / 默认 `-year` 排序 / 分页不相交 / 标题"破折号归一化"、以及对返回项做 `PaperOut.model_validate` 的 schema 回环校验。
   - `tests/test_paper_repository.py`（7 个用例）：绕开 HTTP 直测 `PaperRepository` + `search_papers`，验证加载条数 / 无年份 conf 被丢弃 / `id` 两次加载稳定 (SHA1 前 16 位) / conf 大小写不敏感 / `q="#"` 的"全匹配"语义 / 分页切片不相交。
3. **当前测试状态**：`pytest -q` 共 **24 passed**（原 6 + 新 11 + 新 7），本地 4.6s 跑完，无网络依赖。

刻意没做（继续遵循"不过度设计"）：

- `suggest.py`（OpenAI 调用）暂不加 mock 测试 —— 写 mock 容易变成"测 mock 自身"，等真上线后出 bug 再补。
- 不引入 `pytest-cov`：报告没人看；真要看时加一行 `coverage` 即可。
- 不把 CI 设为 Required check：单人维护项目不需要 branch protection。
- **不把 `ci.yml` 拆成前后端两个 workflow**：当前后端 pytest 4.6s、前端 type-check+lint < 30s，总跑时间已远低于 GH Actions 调度开销；拆分需要付出"两份 concurrency、两个 status check、两份 paths 维护"的成本，ROI 为负。未来满足以下任一条件再拆：(a) 后端测试 > 2 min；(b) 前端引入 E2E（Playwright/Cypress）；(c) 前后端需要不同的 `cancel-in-progress` 策略；(d) Actions 配额吃紧。届时 paths 白名单已经按模块分组好，复制粘贴即可拆分。
