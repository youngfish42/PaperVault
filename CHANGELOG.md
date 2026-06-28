# Changelog

PaperVault 的所有显著改动都记录在此文件中。日期采用 ISO 8601（YYYY-MM-DD）；版本遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Fixed

- **DSL 解析器错误重写引号**（`web-vue/src/utils/queryDsl.ts`）。
  `splitForBackend` 在把短语（例如 `TS="time series"`）转发到后端
  `q` 参数时，会用字面引号重新包裹值（例如发出 `q='"time series"'`）。
  而分词器到达这里时已经剥掉了 `clause.value` 两端的引号，重新包裹
  会让 per-token AND 看到 `'"time'` 与 `'series"'` 两个 token，导致
  整个短语匹配失效。改为直接把分词后的值送进 `qParts`，并补一行注释
  说明为何不能再次包引号。

### Tests

- 本次为前端纯 bugfix，不新增后端测试。前端 `npm` 测试套件
  不依赖被改动的两行；既有用例（19 个）保持通过。
- 建议手动验证：在搜索框输入 `TS="time series"`，确认后端
  收到的 `q` 是 `time series`（无外层引号）。