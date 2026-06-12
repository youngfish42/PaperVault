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
| **Data Artifacts** | pyarrow (Parquet export), huggingface_hub (dataset upload) |
| **AI Features** | OpenAI GPT API (for "Guess You Like" keyword suggestions), tiktoken |
| **Stats / Visualization** | numpy, matplotlib, wordcloud |
| **Build Tool** | Vite 8 with `vite-plugin-compression2` (gzip), `unplugin-auto-import` |

## Project Structure

```
PaperVault/
├── app.py                        # Flask backend API server
├── collector.py                  # Multi-source data collector for paper metadata
├── maintain.py                   # README updater, stats renderer, cache refresh utility
├── data_artifacts.py             # Parquet export & Hugging Face dataset sync helpers
├── requirements.txt              # Python dependencies
├── cache/
│   ├── cache.jsonl.gz            # Gzip-compressed JSON Lines database of all papers (Git LFS)
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
- `HF_TOKEN` / `PAPERVAULT_HF_REPO_ID` - Hugging Face credentials/repo for `data_artifacts.sync_cache_artifacts`
- `PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS`, `PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF` - HF upload retry tuning

### Git LFS

The paper cache file (`cache/cache.jsonl.gz`) is managed by [Git LFS](https://git-lfs.github.com) due to its large size. Make sure you have Git LFS installed before cloning:

```bash
# Install Git LFS (one-time setup)
git lfs install

# Then clone the repository normally
git clone https://github.com/youngfish42/PaperVault.git
```

If you already cloned the repo without LFS, pull the LFS objects with:
```bash
git lfs pull
```

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

CI uses Python 3.10. The collected `cache/cache.jsonl.gz` (and optional Parquet artifact) can be synced to a Hugging Face dataset repo via `data_artifacts.sync_cache_artifacts` when `HF_TOKEN` / `PAPERVAULT_HF_REPO_ID` are configured.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE) for details.

The original project [MLNLP-World/AI-Paper-Collector](https://github.com/MLNLP-World/AI-Paper-Collector) is also licensed under GPL v3.0. When modifying or distributing this project, ensure compliance with GPL v3 requirements including preservation of copyright notices and attribution.
