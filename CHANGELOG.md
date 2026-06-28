# Changelog

PaperVault 的所有显著改动都记录在此文件中。日期采用 ISO 8601（YYYY-MM-DD）；版本遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Fixed

- **多 token 查询的 per-token AND 匹配**（`papervault/services/papers.py`）。
  原 `search_papers` 只把整个查询当作连续子串在标题里匹配，导致
  `"time series agent"` 这类多 token 查询几乎永远返回 0 命中，即便
  语料里几乎所有相关论文都是把三个主题拆成单独的词来讨论
  （例如 ICLR 2026 的
  `TimeSeriesExamAgent: Creating Time Series Reasoning Benchmarks at Scale`）。

  本次改动把查询拆成 token，要求每个 token 至少落在一个被搜索字段
  （标题 / 摘要 / 作者）里，再以加权得分（标题 3×，作者 2×，摘要 1×）
  打破并列，把多字段命中排在单字段命中之前。默认 `sort` 作为副键保留，
  单 token 查询与现有 UI 预设完全保持原有行为。

  向后兼容：`_title_matches` 与 `_author_matches` 仍作为
  `score > 0` 薄包装保留给外部调用方，语义比入选条件更松
  （任一 token 命中即 True），避免历史代码被意外收紧。

### Tests

- 新增 `tests/test_per_token_search.py`（5 个用例）覆盖：
  per-token AND 入选、per-token AND 排除、`field=title` 下的摘要
  兜底、`field=author` 严格模式、加权排序偏好标题证据。
- 注：PR-A（#95）覆盖加载期去重、PR-B（#97）覆盖 DSL 引号修复，
  本 PR 不重复其测试。
- 既有套件保持通过（当前 41/41 含 #94 与 #95/#97 引入的测试）。

## 备注

本 CHANGELOG.md 文件同时由 #95 / #97 / 本 PR 引入，作者在合并时
会收到三轮"新增同名文件"的合并冲突——所有冲突均可通过保留每个
PR 的 `### Fixed` 段落来一次性解决。