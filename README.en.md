<p align="center">
<h1 align="center"> <img src="./pics/icon/ai.png" width="30" /> PaperVault</h1>
</p>
<p align="center">
  <strong>English</strong> | <a href="README.md">简体中文</a>
</p>

## :jack_o_lantern: Project Introduction

PaperVault is a fully automated tool for collecting and retrieving academic papers in artificial intelligence, covering top-tier conferences and journals across natural language processing, computer vision, machine learning, data mining, databases, speech, systems, security, networking, and theoretical computer science.

## 🚧 Project Status

> **This project is actively under construction.**

### Recent Update Brief

<!-- recent-update-start -->

- 📅 **Last Updated**: 2026-06-13
- 🆕 **New Papers This Update**: 30,986
- 📢 **New Conferences This Update**: 1
- 📊 **Database Scale**: 666,444 papers / 120 publication series / 181,855 with abstracts / 29,954 with code

<!-- recent-update-end -->

### Current Phase
<!-- auto-summary-start -->

- The database contains **660,000+** papers spanning 120+ top-tier conferences and journals across NLP, CV, ML, DM, DB, and Speech.

<!-- auto-summary-end -->

### Next Steps
- Upgrade the frontend and backend stack for a better search experience and UI.
- Redeploy and relaunch the web search service.

## :bar_chart: Data Statistics

<!-- stats-start -->

<p align="center">
  <img src="./pics/stats/stats_overview.svg" alt="Statistics Overview" width="850" />
</p>

<p align="center">
  <img src="./pics/stats/papers_by_category.svg" alt="Papers by Research Field" width="850" />
</p>

<p align="center">
  <img src="./pics/stats/papers_by_year.svg" alt="Annual Paper Collection Trend" width="850" />
</p>

<p align="center">
  <img src="./pics/stats/wordcloud.svg" alt="Publication Series Word Cloud" width="900" />
</p>

📊 [View Interactive Statistics](./docs/stats.html)

<!-- stats-end -->

## :inbox_tray: Dataset Access

The core data artifact `cache/cache.jsonl.gz` (papers with titles, authors, abstracts, links, and code URLs) is published as a companion dataset on Hugging Face:

> 📦 **Hugging Face Dataset: [`youngfish42/papervault-cache`](https://huggingface.co/datasets/youngfish42/papervault-cache)**
>
> Available in both gzip-compressed JSON Lines (`cache/cache.jsonl.gz`) and Parquet (`cache/papers.parquet`).

### Option 1: Download the dataset only (recommended for getting started)

For users who only want to analyze paper metadata, without cloning this repo:

```bash
pip install huggingface_hub

huggingface-cli download youngfish42/papervault-cache \
    cache/cache.jsonl.gz --repo-type dataset --local-dir .
```

Read example:

```python
import gzip, json
with gzip.open("cache/cache.jsonl.gz", "rt", encoding="utf-8") as fh:
    for line in fh:
        paper = json.loads(line)
        # {"conf", "paper_name", "paper_authors", "paper_url",
        #  "paper_abstract", "paper_code"}
```

If you prefer a DataFrame:

```python
import pandas as pd
from huggingface_hub import hf_hub_download

path = hf_hub_download(
    repo_id="youngfish42/papervault-cache",
    filename="cache/papers.parquet",
    repo_type="dataset",
)
df = pd.read_parquet(path)
```

### Option 2: Auto-sync from this project

For developers who want to run the web search service, perform incremental collection, or re-render the README. Every entry script fetches the latest cache from Hugging Face on startup; you only need to configure two environment variables:

```bash
cp .env.example .env
# Edit .env and fill in:
#   HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
#   PAPERVAULT_HF_REPO_ID=youngfish42/papervault-cache

pip install -r requirements.txt
python app.py            # Start the web search server
```

> For details on the sync mechanism, concurrency control, offline options such as `PAPERVAULT_OFFLINE`, and how to host the dataset yourself, see [TECHNICAL.md](TECHNICAL.md) and [AGENTS.md](AGENTS.md).

## :open_book: Coverage

<!-- confs-list-start -->

<details>
<summary><b>Computer Architecture / HPC / Storage</b> (17 series)</summary>

- **ASPLOS** 2000-2026 (23 editions)
- **ATC** 2007-2025 (19 editions)
- **DAC** 2000-2025 (26 editions)
- **EUROSYS** 2006-2026 (21 editions)
- **FAST** 2002-2026 (24 editions)
- **HPCA** 2000-2026 (27 editions)
- **HPDC** 2000-2025 (26 editions)
- **ISCA** 2000-2025 (26 editions)
- **MICRO** 2000-2025 (26 editions)
- **PPOPP** 2001-2026 (24 editions)
- **SC** 2000-2025 (26 editions)
- **TACO** 2004-2026 (23 editions)
- **TC** 2000-2026 (27 editions)
- **TCAD** 2000-2026 (27 editions)
- **TOCS** 2000-2026 (27 editions)
- **TOS** 2005-2026 (21 editions)
- **TPDS** 2000-2026 (25 editions)

</details>
<details>
<summary><b>Computer Networks</b> (7 series)</summary>

- **INFOCOM** 2000-2025 (26 editions)
- **JSAC** 2000-2026 (27 editions)
- **MOBICOM** 2000-2025 (25 editions)
- **NSDI** 2004-2026 (23 editions)
- **SIGCOMM** 2000-2025 (26 editions)
- **TMC** 2002-2026 (25 editions)
- **TON** 2000-2026 (27 editions)

</details>
<details>
<summary><b>Network & Information Security</b> (9 series)</summary>

- **CCS** 2000-2025 (26 editions)
- **CRYPTO** 2000-2025 (26 editions)
- **EUROCRYPT** 2000-2026 (27 editions)
- **JOC** 2000-2026 (27 editions)
- **NDSS** 2000-2026 (27 editions)
- **SP** 2000-2025 (26 editions)
- **TDSC** 2004-2026 (23 editions)
- **TIFS** 2006-2026 (21 editions)
- **USS** 2000-2025 (26 editions)

</details>
<details>
<summary><b>Software Engineering / Systems / PL</b> (14 series)</summary>

- **ASE** 2000-2025 (26 editions)
- **FM** 2001-2026 (17 editions)
- **FSE** 2000-2023 (24 editions)
- **ICSE** 2000-2025 (26 editions)
- **ISSTA** 2000-2024 (22 editions)
- **OOPSLA** 2000-2016 (17 editions)
- **OSDI** 2000-2025 (16 editions)
- **PLDI** 2000-2022 (23 editions)
- **POPL** 2000-2017 (18 editions)
- **SOSP** 2001-2025 (14 editions)
- **TOPLAS** 2000-2026 (25 editions)
- **TOSEM** 2000-2026 (25 editions)
- **TSC** 2008-2026 (19 editions)
- **TSE** 2000-2026 (27 editions)

</details>
<details>
<summary><b>Database / Data Mining / IR</b> (15 series)</summary>

- **CIKM** 2000-2025 (26 editions)
- **ECIR** 2002-2026 (25 editions)
- **ICDE** 2000-2025 (26 editions)
- **ICDM** 2001-2025 (25 editions)
- **KDD** 2000-2026 (27 editions)
- **RECSYS** 2007-2025 (19 editions)
- **SIGIR** 2000-2025 (26 editions)
- **SIGMOD** 2000-2022 (23 editions)
- **TKDE** 2000-2025 (26 editions)
- **TODS** 2000-2026 (27 editions)
- **TOIS** 2000-2025 (23 editions)
- **VLDB** 2008-2025 (18 editions)
- **VLDBJ** 2000-2026 (27 editions)
- **WSDM** 2008-2026 (19 editions)
- **WWW** 2001-2026 (26 editions)

</details>
<details>
<summary><b>Theoretical Computer Science</b> (10 series)</summary>

- **ALT** 2000-2025 (26 editions)
- **CAV** 2000-2025 (26 editions)
- **COLT** 2000-2025 (24 editions)
- **FOCS** 2000-2025 (26 editions)
- **IANDC** 2000-2026 (27 editions)
- **LICS** 2000-2025 (26 editions)
- **SICOMP** 2000-2026 (26 editions)
- **SODA** 2000-2026 (27 editions)
- **STOC** 2000-2025 (26 editions)
- **TIT** 2000-2026 (27 editions)

</details>
<details>
<summary><b>Computer Graphics & Multimedia</b> (11 series)</summary>

- **BMVC** 2000-2024 (25 editions)
- **ICME** 2000-2025 (25 editions)
- **IEEEVIS** 2000-2024 (13 editions)
- **MICCAI** 2000-2025 (26 editions)
- **MM** 2000-2025 (26 editions)
- **SIGGRAPH** 2000-2025 (13 editions)
- **TIP** 2000-2026 (27 editions)
- **TMM** 2000-2026 (27 editions)
- **TOG** 2000-2026 (25 editions)
- **TVCG** 2000-2026 (27 editions)
- **VR** 2000-2026 (27 editions)

</details>
<details>
<summary><b>Artificial Intelligence</b> (23 series)</summary>

- **AAAI** 2000-2026 (24 editions)
- **ACL** 2000-2025 (26 editions)
- **AI** 2000-2026 (27 editions)
- **AISTATS** 2001-2025 (16 editions)
- **COLING** 2000-2025 (14 editions)
- **CVPR** 2013-2026 (14 editions)
- **EACL** 2003-2026 (10 editions)
- **ECCV** 2018-2024 (4 editions)
- **EMNLP** 2000-2025 (26 editions)
- **ICCV** 2013-2025 (7 editions)
- **ICLR** 2019-2026 (8 editions)
- **ICML** 2000-2025 (26 editions)
- **IJCAI** 2001-2025 (18 editions)
- **IJCV** 2000-2026 (27 editions)
- **JMLR** 2000-2026 (26 editions)
- **MLJ** 2000-2026 (27 editions)
- **MLSYS** 2019-2025 (7 editions)
- **NAACL** 2000-2025 (18 editions)
- **NIPS** 2000-2025 (26 editions)
- **TNNLS** 2000-2025 (26 editions)
- **TPAMI** 2000-2026 (27 editions)
- **UAI** 2000-2025 (26 editions)
- **WACV** 2020-2026 (7 editions)

</details>
<details>
<summary><b>Human-Computer Interaction & Ubicomp</b> (5 series)</summary>

- **CHI** 2000-2026 (27 editions)
- **CSCW** 2000-2017 (13 editions)
- **TOCHI** 2000-2026 (27 editions)
- **UBICOMP** 2000-2019 (19 editions)
- **UIST** 2000-2025 (26 editions)

</details>
<details>
<summary><b>Speech</b> (3 series)</summary>

- **ICASSP** 2000-2025 (26 editions)
- **INTERSPEECH** 2000-2025 (26 editions)
- **TASLP** 2000-2024 (25 editions)

</details>
<details>
<summary><b>Interdisciplinary / Comprehensive / Emerging</b> (6 series)</summary>

- **BIOINFORMATICS** 2000-2026 (26 editions)
- **ISWC** 2000-2022 (23 editions)
- **JACM** 2000-2026 (26 editions)
- **PROCIEEE** 2000-2025 (26 editions)
- **RTSS** 2000-2025 (26 editions)
- **SCIS** 2001-2026 (26 editions)

</details>


<!-- confs-list-end -->

## :warning: Disclaimer

Due to limitations in data sources and retrieval mechanisms, we can not guarantee that the papers found will meet your needs. In addition, all the results come from [DBLP](https://dblp.org/), [ACL](https://aclanthology.org/), [NIPS](https://papers.nips.cc/), [OpenReview](https://openreview.net/), if this violates your copyright, you can contact us at any time, we will delete it as soon as possible, thank you:)

## :scroll: Acknowledgements

This project is forked from [MLNLP-World/AI-Paper-Collector](https://github.com/MLNLP-World/AI-Paper-Collector) and is now developed independently as **PaperVault**. We sincerely thank the original authors and contributors for laying the foundation. This project continues under the [GNU General Public License v3.0](LICENSE).

---

📄 [Technical Details](TECHNICAL.md)
