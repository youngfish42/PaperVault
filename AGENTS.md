# PaperVault - Agent Development Guide

## Project Overview

PaperVault is a fully-automated web service that collects, curates and searches AI / ML research-paper **metadata** from top-tier academic conferences and journals. It aggregates ACL Anthology, OpenReview, CVF Open Access, NeurIPS Proceedings, DBLP and other sources into a single, continuously-updated corpus and exposes it both as a downloadable dataset (on Hugging Face) and as a ready-to-use search website. The exact, machine-rendered coverage and corpus scale live in `README.md` (the `<!-- recent-update-* -->` / `<!-- stats-* -->` blocks rebuilt by `maintain.py`); do **not** hard-code these numbers in this document.

The backend has been refactored into a dedicated **`papervault/` Python package** built around a Flask application factory (`papervault.create_app`) and a versioned **`/api/v1/*`** REST surface. The legacy unversioned endpoints (`/api/search`, `/api/get_guess_you_like`) have been removed; the design notes for this refactor live in `docs/refactor-plan.md`.

This project was originally forked from [MLNLP-World/AI-Paper-Collector](https://github.com/MLNLP-World/AI-Paper-Collector) and is now developed independently under the name **PaperVault**.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.8+ (CI uses 3.10), Flask 3.x, Werkzeug 3.x, **application factory** (`papervault.create_app`), **Pydantic v2** request/response schemas (`papervault/schemas.py`), unified JSON error envelope (`papervault/errors.py`), request-id structured logging (`papervault/logging.py`); `Settings` is a plain `@dataclass(frozen=True)` driven by env vars (`papervault/config.py`) — **not** `pydantic-settings` |
| **Frontend** | Vue 3.5 (Composition API + `<script setup>`), TypeScript 5.9, Vite 8, Vue Router 4 (hash mode), in-house **i18n** layer (`src/utils/i18n.ts`), WoS-style **query DSL** parser/evaluator (`src/utils/queryDsl.ts` + `src/utils/fields.ts`) powering both Smart Search and the dedicated Advanced Search view |
| **UI Framework** | Element Plus 2.14 (auto-imported via `unplugin-vue-components`), `@vueuse/core` |
| **HTTP Client** | Axios 1.x |
| **Data Collection** | BeautifulSoup4, Requests, PyYAML, tqdm, thefuzz / python-Levenshtein |
| **Data Artifacts** | huggingface_hub (dataset upload with `parent_commit` optimistic locking) |
| **AI Features** | OpenAI / DeepSeek-compatible chat API (for "Guess You Like" keyword suggestions, provider selectable via `PAPERVAULT_SUGGEST_PROVIDER`), tiktoken |
| **Stats / Visualization** | numpy, matplotlib, wordcloud |
| **Build Tool** | Vite 8 with `vite-plugin-compression2` (gzip), `unplugin-auto-import`, `unplugin-vue-components` (auto-generated `auto-imports.d.ts` / `components.d.ts`) |
| **Tests** | Backend: `pytest` under `tests/` (`pytest.ini` → `testpaths=tests`); Frontend: zero-dependency `node:test` regression suite for the DSL parser (`web-vue/src/utils/__tests__/queryDsl.test.mjs`) |
| **Automation** | Playwright (used by `scripts/capture_screenshot.py` to regenerate the README hero screenshot) |

## Project Structure

```
PaperVault/
├── app.py                        # Thin Flask entrypoint: builds `app = create_app(settings)`; no business logic here
├── collector.py                  # Multi-source data collector for paper metadata
├── maintain.py                   # README updater, stats renderer, cache refresh utility
├── data_artifacts.py             # Hugging Face dataset sync helpers (cache.jsonl.gz upload with parent_commit optimistic locking)
├── requirements.txt              # Python dependencies (includes pydantic>=2.6,<3, python-dotenv, openai, huggingface_hub, …)
├── pytest.ini                    # `testpaths=tests`, quiet mode, DeprecationWarning filter
├── papervault/                   # Backend application package (Flask app factory + v1 REST surface)
│   ├── __init__.py               # Re-exports `create_app`
│   ├── app.py                    # `create_app(settings, *, eager_load=True)` — builds Flask app, mounts blueprints under `/api/v1`, registers SPA fallback for non-`/api/` GETs
│   ├── config.py                 # `Settings` `@dataclass(frozen=True)` (env-driven, rebuilt per `get_settings()` call)
│   ├── errors.py                 # `ApiError` + JSON error-envelope handlers (HTTPException + generic 500)
│   ├── logging.py                # `configure_logging` + `install_request_id` (X-Request-ID middleware)
│   ├── schemas.py                # Pydantic v2 models: `PaperOut`, `ConfOut`, `PageMeta`, `PaperSearchParams`, `SuggestRequest`, `SuggestResponse`, …
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/                   # All v1 blueprints (each mounted with `url_prefix="/api/v1"`)
│   │       ├── __init__.py       # Re-exports `health_bp`, `confs_bp`, `papers_bp`, `suggest_bp`
│   │       ├── health.py         # `GET /api/v1/healthz`
│   │       ├── confs.py          # `GET /api/v1/confs`
│   │       ├── papers.py         # `GET /api/v1/papers` (DSL-aware, Pydantic-validated)
│   │       └── suggest.py        # `POST /api/v1/suggest`
│   └── services/
│       ├── __init__.py
│       ├── papers.py             # `PaperRepository` (lazy/eager cache load) + `search_papers` / `SearchCriteria`
│       └── suggest.py            # `suggest_keywords` (OpenAI/DeepSeek provider abstraction)
├── tests/                        # Backend pytest suite (runs offline against fixture cache via `PAPERVAULT_OFFLINE=1`)
│   ├── __init__.py
│   ├── conftest.py               # `client_with_sample` fixture and sample-cache factories
│   ├── test_api_v1.py            # End-to-end coverage for the v1 blueprints + error envelope
│   ├── test_paper_repository.py  # `PaperRepository` load / index behaviour
│   └── test_papers_search.py     # Behavioural tests for `/api/v1/papers` and `/api/v1/confs`
├── cache/
│   ├── cache.jsonl.gz            # Gzip-compressed JSON Lines database of all papers (stored on Hugging Face; git-ignored locally)
│   ├── collect_progress.json     # Per-URL incremental collection progress
│   ├── abstract_backfill_progress.jsonl.gz  # Abstract backfill progress (JSONL.gz on Hugging Face; git-ignored locally)
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
│   ├── fetch_abstracts.py            # Multi-source abstract backfill (Crossref/S2/arXiv/OpenAlex)
│   ├── fetch_openreview_abstracts.py # OpenReview-only abstract backfill (v2 batch → v1 fallback) for ICLR/NeurIPS forums
│   ├── fetch_code_links.py           # Extract GitHub code links from abstracts
│   └── capture_screenshot.py         # Playwright-driven README hero screenshot regenerator (1280x540 @ 2x; assumes backend :5001 + Vite :8080)
├── docs/                         # Auxiliary docs & generated reports
│   ├── refactor-plan.md          # Design notes for the `papervault/` package + `/api/v1` REST refactor (referenced by `app.py`)
│   ├── automation_plan.md
│   ├── execution_guide.md
│   ├── source_analysis.md
│   ├── abstract_backfill_progress.md
│   └── stats.html                # Generated stats page
├── web-vue/                      # Vue 3 frontend application
│   ├── package.json              # Scripts: `dev`, `build` (= type-check + build-only), `type-check`, `lint`, `lint:check`, `preview`
│   ├── vite.config.ts            # Builds to ../static, dev server on :8080, `/api` proxied via `VUE_APP_BASE_URL` (defaults to backend :5001)
│   ├── tsconfig.json
│   ├── index.html
│   ├── auto-imports.d.ts         # Auto-generated by `unplugin-auto-import` (do not edit by hand)
│   ├── components.d.ts           # Auto-generated by `unplugin-vue-components` (do not edit by hand)
│   ├── env.d.ts
│   ├── src/
│   │   ├── main.ts               # App entry point
│   │   ├── App.vue               # Root component
│   │   ├── router/index.ts       # Vue Router (hash mode); routes `/`, `/advanced`, `/about`
│   │   ├── api/paper.ts          # Axios calls against the v1 surface: `/api/v1/papers`, `/api/v1/confs`, `/api/v1/suggest`
│   │   ├── views/
│   │   │   ├── HomeView.vue              # Landing + Smart Search (DSL-aware single-box)
│   │   │   ├── AdvancedSearchView.vue    # Visual query builder that compiles to the WoS-style DSL
│   │   │   └── AboutView.vue             # About / info page
│   │   ├── components/
│   │   │   ├── SearchResultList.vue      # Results display with pagination / export
│   │   │   ├── ConfsTree.vue             # Conference / year filter tree
│   │   │   └── GuessYourLike.vue         # AI keyword suggestions panel
│   │   ├── icons/element-icons.ts        # Element Plus icon registrations
│   │   ├── types/error-code-type.ts      # Shared HTTP error code typing
│   │   ├── assets/                       # Global styles & images
│   │   └── utils/
│   │       ├── axios.ts                  # HTTP client; respects `VUE_APP_BASE_URL`
│   │       ├── file.ts                   # CSV / TXT export utilities
│   │       ├── i18n.ts                   # Lightweight in-house i18n (CN / EN)
│   │       ├── fields.ts                 # Field metadata (display names, validators) for the query DSL
│   │       ├── queryDsl.ts               # WoS-style query DSL parser / splitter / evaluator
│   │       └── __tests__/
│   │           └── queryDsl.test.mjs     # `node:test` regression suite for the DSL parser
│   └── public/                   # Static assets
├── .github/
│   ├── owner.yml
│   └── workflows/
│       ├── ci.yml                    # PR/push gate: backend `pytest -q` (offline) + frontend `type-check` + `lint:check`
│       ├── discover_and_update.yml   # Auto-discover new conferences
│       ├── collect_papers.yml        # Weekly incremental paper collection
│       ├── backfill_abstracts.yml    # Periodic abstract backfill (every 6h)
│       └── update_readme.yml         # Manual README refresh
├── pics/                         # Icons, screenshots, profile images, generated stats charts
├── README.md / README.en.md      # Auto-maintained README (CN / EN); coverage and corpus numbers live in `<!-- recent-update-* -->` / `<!-- stats-* -->` blocks rendered by `maintain.py`
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

The Flask server runs on `http://127.0.0.1:5001` by default. Override the bind address with `HOST` / `PORT` env vars; debug mode is toggled by `FLASK_DEBUG=1`.

**Required / Optional Environment Variables:**
- `OPENAI_API_KEY` - API key for the "Guess You Like" feature (works for both OpenAI and DeepSeek when `PAPERVAULT_SUGGEST_PROVIDER=deepseek`)
- `OPENAI_API_BASE` - OpenAI API base URL (optional, defaults to official endpoint)
- `PAPERVAULT_SUGGEST_PROVIDER` - Suggestion provider, `deepseek` (default) or `openai`
- `PAPERVAULT_OPENAI_MODEL` / `PAPERVAULT_DEEPSEEK_MODEL` / `PAPERVAULT_DEEPSEEK_BASE_URL` - Per-provider overrides (see `papervault/config.py` for the full list)
- `PAPERVAULT_LOG_LEVEL` - Backend log level (default `INFO`)
- `PAPERVAULT_CORS_ORIGINS` - Comma-separated CORS allow-list (empty by default)
- `PAPERVAULT_MAX_PAGE_SIZE` / `PAPERVAULT_DEFAULT_PAGE_SIZE` - Pagination guards for `/api/v1/papers` (defaults 200 / 50)
- `CONTACT_EMAIL` - Contact email injected into `User-Agent` for discovery / scraping (default `im.young@foxmail.com`)
- `HF_TOKEN` / `PAPERVAULT_HF_REPO_ID` - **Required** for cache reads/writes. The authoritative `cache/cache.jsonl.gz` lives on this Hugging Face dataset repo; every entry point calls `data_artifacts.ensure_cache_local()` on startup to pull the latest revision before reading
- `PAPERVAULT_HF_REPO_TYPE` - Repo kind, defaults to `dataset`
- `PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS`, `PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF` - HF upload retry tuning (the upload uses `parent_commit` optimistic locking and will rebase + retry on stale-parent rejections)
- `PAPERVAULT_OFFLINE=1` - Skip HF refresh entirely; only the local copy of the cache will be used (useful for air-gapped dev, when HF is unreachable, or when running `pytest`)

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

1. Every entry point (`app.py`, `collector.do_collect`, `maintain.*`, `scripts/fetch_*`) calls `data_artifacts.ensure_cache_local()` before reading or writing the cache. This downloads the latest revision from HF (if missing or stale) and records the current HF head as the `parent_commit` for the upcoming write. `scripts/fetch_abstracts.py` additionally calls `data_artifacts.ensure_progress_local()` to mirror `cache/abstract_backfill_progress.jsonl.gz` from the same HF dataset, so the backfill ledger is shared across machines and runs.
2. `data_artifacts.upload_to_huggingface()` performs an atomic upload using that `parent_commit`. If another workflow pushed in between, HF rejects with HTTP 412 / "stale parent_commit"; we then re-fetch the new head, rebase locally, and retry up to `PAPERVAULT_HF_UPLOAD_MAX_ATTEMPTS` times (with exponential `PAPERVAULT_HF_UPLOAD_RETRY_BACKOFF`). `sync_cache_artifacts()` now bundles both `cache.jsonl.gz` and the progress file in a single batch so they advance together.
3. All cache-mutating GitHub Actions workflows (`collect_papers.yml`, `backfill_abstracts.yml`, `update_readme.yml`) share a single concurrency group `papervault-cache` so they run strictly serially. `cancel-in-progress: false` ensures an in-flight job is allowed to finish its HF push.
4. PRs created by these workflows **exclude** `cache/cache.jsonl.gz` and `cache/abstract_backfill_progress.jsonl.gz` (`AUTO_*_FILES` / `add-paths`) and only commit small progress / metadata files (`cache/collect_progress.json`, `docs/...`, `pics/...`, `README.md`).

### Abstract backfill progress (JSONL.gz, append-friendly)

`cache/abstract_backfill_progress.jsonl.gz` replaces the legacy
`cache/abstract_backfill_progress.json` (which was a ~14 MB pretty-printed JSON
tracked via Git LFS). The new format:

- First line is a metadata header (`{"_meta": true, "schema": "abstract_backfill_progress/v3", ...}`).
- Each subsequent line is one record: `{"url": ..., "status": ..., "source": ..., "chars": ..., "attempts": ..., "ts": ...}`.
- Same `url` written multiple times — the latest write wins (event-sourcing).
  `load_progress()` collapses to `{url: latest_record}` in memory.
- `save_progress()` appends only diffs since the previous save (cheap on hot
  loops). When the physical line count grows past `live * COMPACTION_RATIO`
  (default 2.0), the file is atomically rewritten via `.tmp` + `os.replace`,
  trimming history back to one line per live URL.
- The file is synchronised to / from Hugging Face exactly like `cache.jsonl.gz`
  (same `parent_commit` optimistic-lock contract).

A one-shot migrator is provided:

```bash
python scripts/migrate_progress_to_jsonl.py            # cache/*.json -> cache/*.jsonl.gz
python scripts/migrate_progress_to_jsonl.py --dry-run  # just report counts
```

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
| `python app.py` | Start Flask backend server (binds to `HOST:PORT`, defaults `127.0.0.1:5001`) |
| `pytest -q` | Run the backend test suite (`tests/`). For CI parity also export `PAPERVAULT_OFFLINE=1` so the cache loader skips HF |
| `python collector.py` | Run collector to update `cache/cache.jsonl.gz` |
| `python maintain.py` | Update README conference list & stats from config files |
| `python maintain.py collect` | Incrementally collect papers for new conferences and update README. Supports `--soft-timeout N` for graceful timeout handling |
| `python maintain.py force` | Force full cache rebuild and README update |
| `python scripts/fetch_abstracts.py` | Multi-source abstract backfill (Crossref → Semantic Scholar → arXiv → OpenAlex). Supports `--phase`, `--conf`, `--chunk-size`, `--retry-failed` |
| `python scripts/fetch_openreview_abstracts.py` | OpenReview-targeted abstract backfill (v2 batch API with v1 fallback). Supports `--conf`, `--year`, `--limit`, `--chunk-size`, `--dry-run` |
| `python scripts/fetch_code_links.py` | Extract GitHub code links from collected abstracts. Supports `--year`, `--retry-failed` |
| `python scripts/capture_screenshot.py` | Regenerate `pics/screenshot/web.jpg` via Playwright (requires backend on :5001 and Vite dev server on :8080). Supports `--url`, `--output`, `--width`, `--height`, `--scale`, `--quality` |
| `python -m discovery.generate_conf` | Generate / merge discovered conference configs |
| `cd web-vue && npm run dev` | Start frontend dev server (Vite, :8080, proxies `/api` to the backend) |
| `cd web-vue && npm run build` | Build frontend for production (runs `type-check` + `build-only` in parallel) |
| `cd web-vue && npm run type-check` | `vue-tsc --noEmit` |
| `cd web-vue && npm run lint` | ESLint + Prettier auto-fix |
| `cd web-vue && npm run lint:check` | ESLint in CI mode (`--max-warnings 0`, no auto-fix) |
| `cd web-vue && node --test src/utils/__tests__/queryDsl.test.mjs` | Run the zero-dependency `node:test` regression suite for the query DSL parser |

## Code Conventions

- **Python**: Follow PEP 8. Use type hints where practical. Prefer module-level constants and `pathlib.Path` for filesystem operations (see `data_artifacts.py`).
- **Backend layout**: New endpoints **must** be added as Flask blueprints under `papervault/api/v1/*`, mounted in `papervault/app.py:create_app` with `url_prefix="/api/v1"`. Validate inputs with **Pydantic v2** schemas in `papervault/schemas.py`; raise `papervault.errors.ApiError` (or let `werkzeug.exceptions.HTTPException` propagate) so the unified JSON envelope handler picks it up — never `return jsonify({"error": ...}), 400` ad hoc. Read settings via `current_app.extensions["settings"]` or `get_settings()`; do **not** import `os.environ` inside handlers.
- **Vue/TypeScript**: Use Composition API with `<script setup>` syntax. Component names use PascalCase. Element Plus components and icons are auto-imported (no manual imports needed for most usage). `auto-imports.d.ts` and `components.d.ts` are generated by `unplugin-*` and **must not** be hand-edited.
- **API Endpoints**: All backend API routes are versioned under `/api/v1/*` in production (proxied via Vite's dev server in development). Legacy `/api/search` and `/api/get_guess_you_like` have been removed — do not re-introduce unversioned endpoints.
- **Query DSL**: User-visible search syntax (Smart Search box and Advanced Search builder) is defined in `web-vue/src/utils/queryDsl.ts` + `fields.ts`; any change to the grammar or field set **must** be paired with a new case in `web-vue/src/utils/__tests__/queryDsl.test.mjs` to prevent the kind of regression that the existing suite already pins down (e.g. `AU="Xiaowen Jiang"` → empty-result).
- **Environment Variables**: Frontend variables must use `VITE_` or `VUE_` prefix (configured in `vite.config.ts`).
- **Imports/Resolvers**: The `@` alias points to `web-vue/src`.

## API Surface (v1)

All endpoints are mounted under `/api/v1` by `papervault/app.py:create_app`. The legacy unversioned routes (`/api/search`, `/api/get_guess_you_like`) have been deleted; do **not** re-introduce them.

| Method & Path | Handler | Request | Response |
|---------------|---------|---------|----------|
| `GET /api/v1/healthz` | `papervault/api/v1/health.py` | — | `{ "status": "ok", "papers": int, "confs": int }` (forces `PaperRepository.ensure_loaded()`) |
| `GET /api/v1/confs` | `papervault/api/v1/confs.py` | — | `{ "items": ConfOut[], "total": int }` with `ConfOut = { name, total, years: [{year, count}] }` |
| `GET /api/v1/papers` | `papervault/api/v1/papers.py` | Query params validated by `PaperSearchParams` (Pydantic v2): `q?`, `field?∈{title,author,any}` (default `title`), `conf?` (repeatable / comma-separated), `since?`, `until?` (1900–2100), `author?`, `sort?∈{±year,±conf,±title}` (default `-year`), `page?≥1`, `size?≥1` (capped at `settings.max_page_size`, default `50`). The `q` field accepts the WoS-style DSL parsed inside `papers.py` | `{ "items": PaperOut[], "meta": PageMeta }` |
| `POST /api/v1/suggest` | `papervault/api/v1/suggest.py` | JSON body validated by `SuggestRequest`: `{ "query": str (1–200), "model"?: str, "max_keywords"?: 1–50 }` | `SuggestResponse = { keywords: string[], timecost_ms: float, model: str }` |

Errors from any route are normalised by `papervault.errors.register_error_handlers` into a unified envelope `{ "error": { "code": str, "message": str, "details"?: any } }` (HTTP status is preserved on the response). `ApiError` / `NotFoundError` / `UpstreamError` are first-class; raw `HTTPException`s have their `name` upcased into `code` and unexpected exceptions degrade to `INTERNAL_ERROR` / 500. The independent request-id is attached to logs and the `X-Request-Id` response header by `papervault.logging` middleware — it is **not** embedded in the JSON body. Non-`/api/` GET requests fall through to a SPA history fallback that serves `static/dist/index.html`.

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
| `ci.yml` | Push / PR on `papervault/**`, `tests/**`, `web-vue/**`, `app.py`, `data_artifacts.py`, `collector.py`, `requirements.txt`, `pytest.ini` (and friends) / Manual | Two parallel jobs: **backend** runs `pytest -q` with `PAPERVAULT_OFFLINE=1`; **frontend** runs `npm run type-check` + `npm run lint:check` on Node 20.19.0. The only quality gate that fails PRs |
| `discover_and_update.yml` | Daily schedule / Manual | Auto-discovers new conference configs and creates PR |
| `collect_papers.yml` | Weekly (Tue 16:00 UTC) / Manual / Push on `conf/**` | Incrementally collects papers with per-URL progress tracking and soft-timeout graceful save; creates PR to `auto-collect-papers` branch |
| `backfill_abstracts.yml` | Monthly schedule (1st of month, UTC) / Manual | Backfills missing abstracts (timeout-aware, ~5h budget) and then re-scans GitHub code links from the freshly backfilled abstracts (`scripts/fetch_code_links.py --year all --retry-failed`), pushes to `auto-backfill-abstracts` branch |
| `update_readme.yml` | Manual (`workflow_dispatch`) | Force rebuilds cache and updates README via PR |

There are five GitHub Actions workflows in total: one **quality-gate** workflow (`ci.yml`) plus four **data-mutation / maintenance** workflows. All Python jobs use **Python 3.10**. The collected `cache/cache.jsonl.gz` is **always** synced to the Hugging Face dataset repo named by `PAPERVAULT_HF_REPO_ID` (HF is now the authoritative store; Git no longer ships this file). The three cache-mutating workflows (`collect_papers.yml`, `backfill_abstracts.yml`, `update_readme.yml`) share the `papervault-cache` concurrency group so they run strictly serially, and each upload uses `parent_commit` optimistic locking so concurrent local runs cannot silently overwrite each other.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE) for details.

The original project [MLNLP-World/AI-Paper-Collector](https://github.com/MLNLP-World/AI-Paper-Collector) is also licensed under GPL v3.0. When modifying or distributing this project, ensure compliance with GPL v3 requirements including preservation of copyright notices and attribution.
