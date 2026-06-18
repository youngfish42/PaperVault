# PaperVault - Agent Development Guide

## Project Overview

PaperVault is a fully-automated web application for collecting and searching AI/ML research papers from top-tier academic conferences. It provides a unified search interface across 40+ conferences spanning NLP, CV, ML, DM, DB, and Speech fields.

This project was originally forked from [MLNLP-World/AI-Paper-Collector](https://github.com/MLNLP-World/AI-Paper-Collector) and is now developed independently under the name **PaperVault**.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.8+ (CI uses 3.10), Flask 3.x, Werkzeug 3.x |
| **Frontend** | Vue 3.5 (Composition API + `<script setup>`), TypeScript 5.9, Vite 8 |
| **UI Framework** | Element Plus 2.14 (auto-imported via `unplugin-vue-components`) |
| **HTTP Client** | Axios 1.x |
| **Data Collection** | BeautifulSoup4, Requests, PyYAML, tqdm, thefuzz / python-Levenshtein |
| **Data Artifacts** | huggingface_hub (dataset upload) |
| **AI Features** | OpenAI GPT API (for "Guess You Like" keyword suggestions), tiktoken |
| **Stats / Visualization** | numpy, matplotlib, wordcloud |
| **Build Tool** | Vite 8 with `vite-plugin-compression2` (gzip), `unplugin-auto-import` |

## Project Structure

```
PaperVault/
├── app.py                        # Flask backend API server
├── collector.py                  # Multi-source data collector for paper metadata
├── maintain.py                   # README updater, stats renderer, cache refresh utility
├── data_artifacts.py             # Hugging Face dataset sync helpers (cache.jsonl.gz upload with parent_commit optimistic locking)
├── requirements.txt              # Python dependencies
├── cache/
│   ├── cache.jsonl.gz            # Gzip-compressed JSON Lines database of all papers (stored on Hugging Face; git-ignored locally)
│   ├── collect_progress.json     # Per-URL incremental collection progress
│   ├── abstract_backfill_progress.json  # Abstract backfill progress tracking
│   └── readme_meta.json          # README rendering metadata snapshot
├── conf/                         # Conference source configurations
│   ├── acl_conf.json             # ACL Anthology sources (NLP)
│   ├── dblp_conf.json            # DBLP sources (mixed venues)
│   ├── iclr_conf.json            # OpenReview ICLR/NeurIPS sources
│   ├── nips_conf.json            # NeurIPS & MLSys proceedings
│   └── thecvf_conf.json          # CVF Open Access (CVPR, ICCV, WACV)
├── discovery/                    # Auto-discovery of new conference editions
│   ├── base.py                   # Discovery base class & shared HTTP utilities
│   ├── acl.py                    # ACL Anthology discovery
│   ├── cvf.py                    # CVF Open Access discovery
│   ├── dblp.py                   # DBLP discovery
│   ├── nips.py                   # NeurIPS proceedings discovery
│   ├── openreview.py             # OpenReview (ICLR) discovery
│   └── generate_conf.py          # Generate/merge conf JSON from discovery results
├── scripts/                      # Maintenance / data enrichment scripts
│   ├── fetch_abstracts.py        # Multi-source abstract backfill (Crossref/S2/arXiv/OpenAlex)
│   ├── fetch_openreview_abstracts.py  # OpenReview-only abstract backfill (v2 batch → v1 fallback) for ICLR/NeurIPS forums
│   └── fetch_code_links.py       # Extract GitHub code links from abstracts
├── docs/                         # Auxiliary docs & generated reports
│   ├── automation_plan.md
│   ├── execution_guide.md
│   ├── source_analysis.md
│   ├── abstract_backfill_progress.md
│   └── stats.html                # Generated stats page
├── web-vue/                      # Vue 3 frontend application
│   ├── package.json
│   ├── vite.config.ts            # Vite config: builds to ../static, dev server on :8080
│   ├── tsconfig.json
│   ├── index.html
│   ├── src/
│   │   ├── main.ts               # App entry point
│   │   ├── App.vue               # Root component
│   │   ├── router/index.ts       # Vue Router (hash mode)
│   │   ├── api/paper.ts          # API calls: /search, /get_guess_you_like
│   │   ├── views/
│   │   │   ├── HomeView.vue              # Main search page
│   │   │   └── AboutView.vue             # About / info page
│   │   ├── components/
│   │   │   ├── SearchResultList.vue      # Results display with pagination/export
│   │   │   ├── ConfsTree.vue             # Conference/year filter tree
│   │   │   ├── GuessYourLike.vue         # AI keyword suggestions panel
│   │   │   └── AdvancedSettingDlg.vue    # Filters dialog (year, author, confs)
│   │   ├── icons/element-icons.ts        # Element Plus icon registrations
│   │   ├── types/error-code-type.ts      # Shared HTTP error code typing
│   │   ├── assets/                       # Global styles & images
│   │   └── utils/
│   │       ├── axios.ts          # HTTP client with proxy config
│   │       └── file.ts           # CSV/TXT export utilities
│   └── public/                   # Static assets
├── .github/
│   ├── owner.yml
│   └── workflows/
│       ├── discover_and_update.yml   # Auto-discover new conferences
│       ├── collect_papers.yml        # Weekly incremental paper collection
│       ├── backfill_abstracts.yml    # Periodic abstract backfill (every 6h)
│       └── update_readme.yml         # Manual README refresh
├── pics/                         # Icons, screenshots, profile images, generated stats charts
├── README.md / README.en.md      # Auto-maintained README (CN / EN)
├── TECHNICAL.md                  # Technical design notes
└── LICENSE
```

## Development Setup

### Backend

```bash
# Create and activate virtual environment (conda example)
source F:/Miniforge3/etc/profile.d/conda.sh && conda activate llm

# Install dependencies
pip install -r requirements.txt

# Run Flask server
python app.py
```

The Flask server runs on `http://127.0.0.1:5000` by default.

**Required / Optional Environment Variables:**
- `OPENAI_API_KEY` - OpenAI API key for "Guess You Like" feature
- `OPENAI_API_BASE` - OpenAI API base URL (optional, defaults to official endpoint)
- `CONTACT_EMAIL` - Contact email injected into `User-Agent` for discovery / scraping (default `im.young@foxmail.com`)
- `HF_TOKEN` / `PAPERVAULT_HF_REPO_ID` - **Required** for cache reads/writes. The authoritative `cache/cache.jsonl.gz` lives on this Hugging Face dataset repo; every entry point calls `data_artifacts.ensure_cache_local()` on startup to pull the latest revision before reading
- `PAPERVAULT_HF_REPO_TYPE` - Repo kind, defaults to `dataset`
- `PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS`, `PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF` - HF upload retry tuning (the upload uses `parent_commit` optimistic locking and will rebase + retry on stale-parent rejections)
- `PAPERVAULT_OFFLINE=1` - Skip HF refresh entirely; only the local copy of the cache will be used (useful for air-gapped dev or when HF is unreachable)

### Cache Storage (Hugging Face)

`cache/cache.jsonl.gz` is **no longer tracked by Git or Git LFS**. The authoritative copy lives on the Hugging Face dataset repo named by `PAPERVAULT_HF_REPO_ID`. Locally:

```bash
# Clone the repo (no LFS pull is required for cache.jsonl.gz)
git clone https://github.com/youngfish42/PaperVault.git
cd PaperVault

# Configure Hugging Face credentials so the cache can be downloaded
export HF_TOKEN=hf_xxx
export PAPERVAULT_HF_REPO_ID=<your-namespace>/<dataset-repo>

# Any entry point will now fetch the latest cache on startup
python app.py            # also serves the cache to the web UI
python collector.py      # incremental collection
python maintain.py       # README + stats rebuild
```

How synchronisation works:

1. Every entry point (`app.py`, `collector.do_collect`, `maintain.*`, `scripts/fetch_*`) calls `data_artifacts.ensure_cache_local()` before reading or writing the cache. This downloads the latest revision from HF (if missing or stale) and records the current HF head as the `parent_commit` for the upcoming write.
2. `data_artifacts.upload_to_huggingface()` performs an atomic upload using that `parent_commit`. If another workflow pushed in between, HF rejects with HTTP 412 / "stale parent_commit"; we then re-fetch the new head, rebase locally, and retry up to `PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS` times (with exponential `PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF`).
3. All cache-mutating GitHub Actions workflows (`collect_papers.yml`, `backfill_abstracts.yml`, `update_readme.yml`) share a single concurrency group `papervault-cache` so they run strictly serially. `cancel-in-progress: false` ensures an in-flight job is allowed to finish its HF push.
4. PRs created by these workflows **exclude** `cache/cache.jsonl.gz` (`AUTO_*_FILES` / `add-paths`) and only commit progress / metadata files (`cache/collect_progress.json`, `cache/abstract_backfill_progress.json`, `docs/...`, `pics/...`, `README.md`).

### One-time migration from Git LFS

If you are upgrading an existing checkout that still has the LFS-tracked cache:

```bash
# 1) Make sure you have a recent local copy before disconnecting from LFS
ls -lh cache/cache.jsonl.gz

# 2) Pull the new branch / commit that removes LFS tracking, then:
git lfs untrack "cache/cache.jsonl.gz"   # already done in .gitattributes
git rm --cached cache/cache.jsonl.gz     # stop tracking in git index (keeps local file)

# 3) Upload the local copy to Hugging Face once to seed the authoritative repo
python -c "from data_artifacts import sync_cache_artifacts; sync_cache_artifacts(commit_message='Initial migration from Git LFS')"

# 4) From now on, the local copy is git-ignored and synced via HF only.
```

> Note: GitHub LFS bandwidth is **not** consumed any more after the migration. CI runners no longer call `git lfs pull`; they download the cache directly from HF.

### Frontend

```bash
cd web-vue

# Install dependencies (requires Node.js >= 20.19.0)
npm install

# Start development server
npm run dev
```

The Vite dev server runs on `http://localhost:8080` and proxies `/api` to the backend (target controlled by `VUE_APP_BASE_URL`).

**Environment Files:**
- `.env.development` - Development environment variables
- `.env.production` - Production environment variables
- Vite reads variables with `VITE_` or `VUE_` prefix (see `envPrefix` in `vite.config.ts`).

### Build for Production

```bash
cd web-vue
npm run build          # runs `type-check` + `build-only` in parallel
```

This builds the frontend into the `static/` directory at the project root, which Flask serves directly. The build also emits gzipped assets via `vite-plugin-compression2`.

Other frontend scripts:
- `npm run type-check` - run `vue-tsc --noEmit`
- `npm run lint` - ESLint + Prettier auto-fix
- `npm run preview` - preview the production build locally

## Key Commands

| Command | Description |
|---------|-------------|
| `python app.py` | Start Flask backend server |
| `python collector.py` | Run collector to update `cache/cache.jsonl.gz` |
| `python maintain.py` | Update README conference list & stats from config files |
| `python maintain.py collect` | Incrementally collect papers for new conferences and update README. Supports `--soft-timeout N` for graceful timeout handling |
| `python maintain.py force` | Force full cache rebuild and README update |
| `python scripts/fetch_abstracts.py` | Multi-source abstract backfill (Crossref → Semantic Scholar → arXiv → OpenAlex). Supports `--phase`, `--conf`, `--chunk-size`, `--retry-failed` |
| `python scripts/fetch_openreview_abstracts.py` | OpenReview-targeted abstract backfill (v2 batch API with v1 fallback). Supports `--conf`, `--year`, `--limit`, `--chunk-size`, `--dry-run` |
| `python scripts/fetch_code_links.py` | Extract GitHub code links from collected abstracts. Supports `--year`, `--retry-failed` |
| `python -m discovery.generate_conf` | Generate / merge discovered conference configs |
| `cd web-vue && npm run dev` | Start frontend dev server |
| `cd web-vue && npm run build` | Build frontend for production |
| `cd web-vue && npm run lint` | Lint frontend code |

## Code Conventions

- **Python**: Follow PEP 8. Use type hints where practical. Prefer module-level constants and `pathlib.Path` for filesystem operations (see `data_artifacts.py`).
- **Vue/TypeScript**: Use Composition API with `<script setup>` syntax. Component names use PascalCase. Element Plus components and icons are auto-imported (no manual imports needed for most usage).
- **API Endpoints**: All backend API routes are prefixed with `/api/` in production (proxied to `/` in dev by Vite).
- **Environment Variables**: Frontend variables must use `VITE_` or `VUE_` prefix (configured in `vite.config.ts`).
- **Imports/Resolvers**: The `@` alias points to `web-vue/src`.

## Data Sources

The collector fetches paper metadata from:
- [ACL Anthology](https://aclanthology.org/) - NLP conferences
- [OpenReview](https://openreview.net/) - ICLR, NeurIPS
- [OpenAccess.thecvf](https://openaccess.thecvf.com/) - CVPR, ICCV, WACV
- [NeurIPS Proceedings](https://papers.nips.cc/) - NeurIPS, MLSys
- [DBLP](https://dblp.org/) - 30+ mixed venues

Abstract backfill sources (`scripts/fetch_abstracts.py`):
- [Crossref](https://www.crossref.org/) (by DOI)
- [Semantic Scholar](https://www.semanticscholar.org/) (by DOI)
- [arXiv](https://arxiv.org/) (by title)
- [OpenAlex](https://openalex.org/) (by DOI)

Code links are enriched from [MLNLP-World/Top-AI-Conferences-Paper-with-Code](https://github.com/MLNLP-World/Top-AI-Conferences-Paper-with-Code) and via regex extraction from abstracts (`scripts/fetch_code_links.py`).

## CI/CD Workflows

| Workflow | Trigger | Action |
|----------|---------|--------|
| `discover_and_update.yml` | Daily schedule / Manual | Auto-discovers new conference configs and creates PR |
| `collect_papers.yml` | Weekly (Tue 16:00 UTC) / Manual / Push on `conf/**` | Incrementally collects papers with per-URL progress tracking and soft-timeout graceful save; creates PR to `auto-collect-papers` branch |
| `backfill_abstracts.yml` | Monthly schedule (1st of month, UTC) / Manual | Backfills missing abstracts (timeout-aware, ~5h budget) and then re-scans GitHub code links from the freshly backfilled abstracts (`scripts/fetch_code_links.py --year all --retry-failed`), pushes to `auto-backfill-abstracts` branch |
| `update_readme.yml` | Manual (`workflow_dispatch`) | Force rebuilds cache and updates README via PR |

CI uses Python 3.10. The collected `cache/cache.jsonl.gz` is **always** synced to the Hugging Face dataset repo named by `PAPERVAULT_HF_REPO_ID` (HF is now the authoritative store; Git no longer ships this file). All three cache-mutating workflows above share the `papervault-cache` concurrency group so they run strictly serially, and each upload uses `parent_commit` optimistic locking so concurrent local runs cannot silently overwrite each other.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE) for details.

The original project [MLNLP-World/AI-Paper-Collector](https://github.com/MLNLP-World/AI-Paper-Collector) is also licensed under GPL v3.0. When modifying or distributing this project, ensure compliance with GPL v3 requirements including preservation of copyright notices and attribution.
