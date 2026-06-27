# Changelog

PaperVault 的所有显著改动都记录在此文件中。日期采用 ISO 8601（YYYY-MM-DD）；版本遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Fixed

- **加载期去重兜底**（`papervault/services/papers.py`）。
  `scripts/cleanup_cache_dedupe.py`（PR #94）已经提供了离线清洗
  `cache/cache.jsonl.gz` 的能力；但只要 collector 后续运行再次引入
  重复行、或者缓存来自未经过清理脚本的历史文件，搜索索引仍可能
  重复同一篇论文。`PaperRepository._load` 现在按 `Paper.id`
  （`sha1(conf|year|title)[:16]`）在内存索引构建前丢弃重复行，作为
  运行时防御层。启动日志同步显示丢弃数量，便于观察运行时去重率。

  本次改动 **不改** `Paper.id` 计算规则、不动 `collector`、不替换
  PR #94 的离线脚本，仅在加载最末段增加一道安全网。

### Tests

- 新增 `tests/test_load_dedup.py`（3 个用例）覆盖：
  加载期去重生效、不同论文之间互不影响、日志包含丢弃数量。
- PR-A 不修改既有测试：当前套件保持 41/41 通过。