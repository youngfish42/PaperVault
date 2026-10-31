# PaperVault 自动化获取论文清单规划文档

> 目标：将现有"手动维护 conf/*.json → 人工触发采集"的流程，升级为"自动发现新会议 → 自动生成配置 → 自动采集与数据增强"的全自动化流程。
> 分支：`analyze-sources`

---

## 1. 设计原则

1. **最小侵入**：尽量保留 `collector.py` 中成熟的 5 类采集函数，不破坏现有解析逻辑。
2. **配置即代码**：将静态的 `conf/*.json` 升级为"模板 + 生成脚本"，新会议通过脚本自动发现。
3. **可回滚**：自动生成的配置和元数据通过普通 Git 提交直接写入 `main`，保留完整历史，可逐提交 revert，不再依赖人工合并 PR。
4. **渐进式**：先实现配置自动发现，再逐步补充 abstract 补全、代码链接优化等增强功能。

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      自动化采集流水线                         │
├─────────────────────────────────────────────────────────────┤
│  Step 1: 自动发现 (Discovery)                                │
│    ├─ ACLDiscovery      → 扫描 aclanthology.org/events/      │
│    ├─ CVFDiscovery      → 按规则生成 openaccess.thecvf.com   │
│    ├─ NeurIPSDiscovery  → 扫描 papers.nips.cc/paper/{year}   │
│    ├─ MLSysDiscovery    → 扫描 proceedings.mlsys.org         │
│    ├─ OpenReviewDiscovery→ 查询 OpenReview API               │
│    └─ DBLPDiscovery     → 按规则生成 dblp.org 链接           │
│                          ↓                                   │
│  Step 2: 配置生成 (Config Generator)                         │
│    ├─ 合并自动发现结果与现有 conf/*.json                     │
│    ├─ 去重、校验 URL 可访问性                               │
│    └─ 输出新的 conf/*.json                                   │
│                          ↓                                   │
│  Step 3: 论文采集 (Collector)                                │
│    └─ 复用现有 collector.py 逻辑                             │
│                          ↓                                   │
│  Step 4: 数据增强 (Enhancer)  [可选，二期]                   │
│    ├─ AbstractBackfill   → Semantic Scholar / OpenAlex API   │
│    └─ CodeLinkEnrich     → PapersWithCode API                │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 各数据源自动发现策略

### 3.1 ACL Anthology (`acl_conf.json`)

**规律**：
- 会议主页面：`https://aclanthology.org/events/{venue}-{year}/`
- Findings 页面：`https://aclanthology.org/volumes/{year}.findings-{venue}/`
- 支持的 venue：`acl`, `emnlp`, `naacl`, `eacl`, `coling`

**自动发现策略**：
1. 维护 venue 白名单 + 起始年份。
2. 对 `year` 从 `start_year` 到 `current_year`：
   - 尝试 `HEAD` 请求 `events/{venue}-{year}/`。
   - 若返回 200，则加入配置。
   - 同时尝试 `volumes/{year}.findings-{venue}/`，若存在则作为同名的第二条记录。
3. 已有 `tag` 规则可按 venue 模板生成（如 `^/2024.acl*`）。

### 3.2 CVF Open Access (`thecvf_conf.json`)

**规律**：
- URL：`https://openaccess.thecvf.com/{CONF}{YEAR}?day=all`
- CONF 白名单：`CVPR`, `ICCV`, `WACV`, `ECCV`
- ECCV 只在偶数年举办，其余每年举办。

**自动发现策略**：
1. 对 `CVPR/ICCV/WACV`：遍历 `year` 从起始年到当前年，按模板生成 URL，HEAD 校验。
2. 对 `ECCV`：只在偶数年生成。

### 3.3 NeurIPS / MLSys (`nips_conf.json`)

**规律**：
- NeurIPS：`https://papers.nips.cc/paper/{year}`
- MLSys：`https://proceedings.mlsys.org/paper/{year}`
- 两者都是每年一届，URL 规律极强。

**自动发现策略**：
- 直接按年份模板生成，HEAD 校验 200。

### 3.4 OpenReview (`iclr_conf.json`)

**规律**：
- ICLR 和 NeurIPS 近年使用 OpenReview，但 venue 字符串格式多变：
  - `ICLR 2022 Poster`, `ICLR 2023 poster`, `ICLR 2023 notable top 5%` …
  - `NeurIPS 2022 Accept`, `NeurIPS+2022+Accept` …
- API 端点：`https://api.openreview.net/notes?content.venue=...&offset=0&limit=1000`

**自动发现策略**：
1. 调用 OpenReview API 的 `venues` 端点，或遍历已知 pattern。
2. 更稳健的方式：
   - 对 ICLR：访问 `https://api.openreview.net/notes?invitation=ICLR.cc/{year}/Conference/-/Blind_Submission&details=replyCount`，然后按返回结果中的 `content.venue` 去重。
   - 对 NeurIPS：类似，invitation 为 `NeurIPS.cc/{year}/Conference/-/Blind_Submission`。
3. 收集所有 venue 字符串后，生成多条 URL（因每条最多返回 1000 条，需分页）。

### 3.5 DBLP (`dblp_conf.json`)

**规律**：
- 会议：`https://dblp.org/db/conf/{abbrev}/{abbrev}{year}.html`
- 期刊：`https://dblp.org/db/journals/{abbrev}/{abbrev}{vol}.html`（卷号与年份非线性映射）
- 部分会议有多卷（ECCV、MICCAI、ECIR 等）。

**自动发现策略**：
1. **会议**：维护 abbrev 白名单 + 起始年份，按模板生成 URL，HEAD 校验。
2. **期刊**：卷号→年份映射表需手动维护（或爬取 DBLP 期刊主页），暂时保留手动配置，但可提供辅助脚本检测最新卷号。
3. **多卷会议**（ECCV/MICCAI）：先请求主页面，若不存在则尝试 `-1`, `-2` … 直到返回 404。

---

## 4. 实施步骤（Roadmap）

### Phase 1: 基础框架（本次实现）

- [x] **Step 1.1**: 完成数据来源分析（`docs/source_analysis.md`）。
- [x] **Step 1.2**: 完成自动化规划文档（`docs/automation_plan.md`）。
- [x] **Step 1.3**: 创建 `discovery/` 目录，实现 `BaseDiscovery` 抽象类。
- [x] **Step 1.4**: 实现各数据源的 Discovery 类：
  - `ACLDiscovery`
  - `CVFDiscovery`
  - `NeurIPSDiscovery`
  - `MLSysDiscovery`
  - `OpenReviewDiscovery`
  - `DBLPDiscovery`
- [x] **Step 1.5**: 实现 `generate_conf.py`，合并自动发现结果并生成新的 `conf/*.json`。
- [x] **Step 1.6**: 验证新配置与现有 `collector.py` 兼容。

### Phase 2: 集成与优化（后续迭代）

- [x] **Step 2.1**: 在 GitHub Actions 中新增 `discover_and_update.yml`，每日运行一次自动发现，并将配置直接提交到 `main`。
- [ ] **Step 2.2**: 接入 Semantic Scholar / OpenAlex API，批量补全 DBLP 来源论文的 abstract。
- [ ] **Step 2.3**: 接入 PapersWithCode API，替换现有的 GitHub Markdown 代码链接匹配逻辑。
- [x] **Step 2.4**: 优化增量更新粒度，支持单会议强制刷新。
  - 新增 `maintain.py collect` 命令，只收集 conf 中有但 cache 中没有的会议。
  - 新增 GitHub Actions 工作流 `collect_papers.yml`：定时 + conf 文件 push 触发 + 手动触发；自动发现提交后显式派发收集，产物直接提交到 `main`。

---

## 5. 关键风险与缓解

| 风险 | 影响 | 缓解措施 |
|:---|:---|:---|
| DBLP / ACL 网站结构调整 | 采集器失效 | 保留现有解析逻辑作为 fallback；Discovery 只做配置生成，不替代采集器。 |
| OpenReview API 限流 | 配置生成失败 | 增加重试与指数退避；将 OpenReview 配置缓存到本地。 |
| 自动发现的 URL 无效（会议未举办） | 生成错误配置 | 所有 Discovery 在生成前做 HEAD/GET 校验，只保留 200 OK 的链接。 |
| 新配置导致采集到重复论文 | 缓存膨胀 | 在 `collect()` 中已有 `conf_name` 去重机制，保持不变。 |
| 期刊卷号映射错误 | DBLP 期刊配置错误 | 期刊卷号映射表人工审核，脚本只做辅助提示。 |

---

## 6. 预期收益

1. **维护成本**：每年新增会议从"人工 2-4 小时查找 URL"降至工作流自动发现、提交与收集，无需逐次人工复核合并。
2. **覆盖度**：大幅降低遗漏新会议的概率（如 Findings 系列、OpenReview 新 venue）。
3. **数据质量**：为后续 abstract 补全、代码链接优化打下框架基础。
