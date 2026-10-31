# PaperVault 论文数据来源分析报告

> 分析对象：`cache/cache.jsonl.gz`（94,641 条记录，152 个会议/期刊）
> 分析时间：2026-05-23
> 分析分支：`analyze-sources`

---

## 1. URL 域名分布（Top 20）

| 排名 | 域名 | 论文数 | 占比 | 对应数据源 |
|:---:|:---|---:|---:|:---|
| 1 | `doi.org` | 41,960 | 44.3% | DBLP 聚合（IEEE/ACM/Springer/…） |
| 2 | `aclanthology.org` | 12,901 | 13.6% | ACL Anthology |
| 3 | `openaccess.thecvf.com` | 11,654 | 12.3% | CVF Open Access |
| 4 | `openreview.net` | 7,390 | 7.8% | OpenReview（ICLR / 部分 NeurIPS） |
| 5 | `proceedings.mlr.press` | 6,566 | 6.9% | PMLR（ICML / AISTATS / COLT …） |
| 6 | `papers.nips.cc` | 5,660 | 6.0% | NeurIPS 官网 |
| 7 | `ojs.aaai.org` | 5,450 | 5.8% | AAAI 官网 |
| 8 | `www.vldb.org` | 997 | 1.1% | VLDB |
| 9 | `jmlr.org` | 769 | 0.8% | JMLR |
| 10 | `bmvc*` | 863 | 0.9% | BMVC（各年域名不同） |
| 11 | `www.usenix.org` | 140 | 0.1% | USENIX（FAST 等） |
| 12 | `proceedings.mlsys.org` | 137 | 0.1% | MLSys |
| 13 | `web.archive.org` | 75 | <0.1% | 归档链接 |
| 14 | `www.isca-speech.org` | 59 | <0.1% | ISCA Speech |
| 15 | `ieeexplore.ieee.org` | 6 | <0.1% | IEEE Xplore（直接） |

**结论**：
- **DBLP 是最大入口**（44.3%），但论文最终落地到各出版社的 DOI，导致 `doi.org` 占比最高。
- **三大自托管平台**（ACL Anthology、CVF、OpenReview/PMLR/papers.nips.cc）合计约 44.2%，与 DBLP 渠道几乎平分秋色。
- 其余为各会议独立域名或出版社直连。

---

## 2. 会议级来源与采集器映射

`collector.py` 中定义了 5 类采集函数，对应 5 组配置文件：

| 采集器 | 配置文件 | 解析方式 | 典型会议 | 缓存中论文数（估算） |
|:---|:---|:---|:---|---:|
| `search_from_acl` | `acl_conf.json` | HTML (BeautifulSoup) | ACL, EMNLP, NAACL, COLING, Findings | ~12,900 |
| `search_from_thecvf` | `thecvf_conf.json` | HTML (BeautifulSoup) | CVPR, ICCV, WACV | ~11,600 |
| `search_from_iclr` | `iclr_conf.json` | OpenReview API (JSON) | ICLR, NeurIPS (OpenReview) | ~7,400 |
| `search_from_nips` | `nips_conf.json` | HTML (BeautifulSoup) | NeurIPS, MLSys | ~5,800 |
| `search_from_dblp` | `dblp_conf.json` | HTML (BeautifulSoup) | AAAI, ICML, KDD, CIKM, MICCAI, … | ~50,000+ |

### 2.1 DBLP 子来源细分

DBLP 采集的论文虽然入口统一，但最终 `paper_url` 指向多个出版社：

| 出版社/平台 | 典型会议 | 说明 |
|:---|:---|:---|
| `doi.org` | ICASSP, ECCV, INTERSPEECH, MICCAI, MM, KDD, CIKM, SIGIR, TIP, TPAMI, TNNLS, TKDE, TASLP… | DBLP 的 `ee` 字段指向 DOI |
| `ojs.aaai.org` | AAAI | 近年 AAAI 改用 Open Journal System |
| `proceedings.mlr.press` | ICML, AISTATS, COLT | DBLP 链接到 PMLR |
| `openaccess.thecvf.com` | CVPR, ICCV | 少量通过 DBLP 链接 |
| `aclanthology.org` | ACL 系列 | 少量通过 DBLP 链接 |
| `www.vldb.org` | VLDB | 期刊形式，DBLP 链接到 PVLD |
| `jmlr.org` | JMLR | 直接链接 |
| `www.usenix.org` | FAST | USENIX 会议 |
| `bmvc*` | BMVC | 各年独立域名 |

---

## 3. 现有采集流程的瓶颈与问题

### 3.1 配置维护完全手动
- `conf/*.json` 中每个年份、每个会议都需要手动写 URL。
- 新增一年会议时，需要人工查找 OpenReview venue 字符串、ACL events 页面、DBLP 链接等，**门槛高、易遗漏**。
- 例如：CVPR2024/2025、ICLR2024/2025、ACL2024/2025 均未在配置中出现。

### 3.2 Abstract 获取受限
- `search_from_dblp` 中的 `search_abs_from_dblp` 被整体注释（`# due to limits`）。
- 导致 DBLP 来源的 5 万余篇论文**全部没有 abstract**，严重影响搜索质量。

### 3.3 代码链接来源单一且易失效
- `add_code_links` 依赖外部仓库 `MLNLP-World/Top-AI-Conferences-Paper-with-Code` 的 Markdown 列表。
- 该仓库更新不及时，且标题匹配是简单的 lower-case 字符串比较，**匹配率不高**。

### 3.4 增量粒度粗
- 当前增量更新以**整个会议**为单位：只要某个 `conf_name` 在缓存中，就跳过该会议的全部采集。
- 无法处理"会议已发布但后续有勘误/增刊"的情况，也无法对单个会议做部分刷新。

### 3.5 定时自动化触发
- `discover_and_update.yml` 已配置每日定时运行，自动扫描并发现新会议配置。

---

## 4. 关键结论

1. **当前论文获取是"多源入口 + 统一缓存"模式**：5 种采集器覆盖 5 大类数据源，DBLP 作为兜底聚合源承担了最大流量。
2. **最大的可改进点是配置自动化**：如果把"手动写 conf/*.json"升级为"自动发现新会议并生成配置"，每年可减少数小时人工维护成本，且大幅降低遗漏率。
3. **次优改进点是补充 DBLP 论文的 abstract**：可以考虑通过 Semantic Scholar API、OpenAlex API 或 CrossRef 批量补全，避免直接访问出版社页面被封禁。
4. **代码链接可以引入 Papers With Code 官方 API**，替代手工维护的 Markdown 列表。
