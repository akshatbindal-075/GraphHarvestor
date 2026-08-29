# GraphHarvester

> Production-grade async data ingestion pipeline for AI startup intelligence.
> Scrapes 2,000+ research papers, 1,000+ startups & products, and fresh jobs/news daily —
> enriched with LLM extraction, entity resolution, and written to Google Sheets.

---

## Architecture Overview

GraphHarvester is designed as an end-to-end, multi-stage async ingestion and data processing pipeline.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 STAGE 1 & 2: SCRAPING                                  │
│  ┌────────────────┐  ┌─────────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ ArXiv Scraper  │  │ PapersWithCode/Open │  │ Startup Scraper │  │ Product Scraper │ │
│  │   (ArXiv API)  │  │    Alex (API/⭐)    │  │  (YC Algolia)   │  │   (HN/GraphQL)  │ │
│  └───────┬────────┘  └──────────┬──────────┘  └────────┬────────┘  └────────┬────────┘ │
│          │                      │                      │                    │          │
│          └──────────────────────┼──────────────────────┴────────────────────┘          │
│                                 │                                                      │
│                       ┌─────────┴─────────┐                                            │
│                       │ News/Jobs Scrapers│                                            │
│                       │ (RSS/JSON/Algolia)│                                            │
│                       └─────────┬─────────┘                                            │
└─────────────────────────────────┼──────────────────────────────────────────────────────┘
                                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                             STAGE 3: LLM EXTRACTION CHAIN                              │
│              Gemini 3.6 Flash  ──►  Groq 8B  ──►  OpenRouter  ──►  DeepSeek             │
│              (Tenacity exponential backoff with jitter on HTTP 429 rate-limits)        │
└─────────────────────────────────┼──────────────────────────────────────────────────────┘
                                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                           STAGE 4: ENTITY RESOLUTION & DEDUP                           │
│      - Exact & Fuzzy String Matching (rapidfuzz @ 85% threshold)                       │
│      - Seed Entity List (50 canonical AI entities + aliases)                           │
│      - Deduplication Cache (seen_urls.json / mtime fallback)                           │
└─────────────────────────────────┼──────────────────────────────────────────────────────┘
                                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        STAGE 5: BATCH WRITER & GRAPH INTEGRATION                       │
│      - Google Sheets API (gspread, OAuth 2.0 / Service Account)                        │
│      - Batching (500 rows/batch, 1.2s delay to prevent 429 rate limits)                │
│      - PostgreSQL / Neo4j Graph Integration Ready                                      │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
src/
  scrapers/
    arxiv_scraper.py          ArXiv API  (1,000+ research papers)
    paperswithcode_scraper.py OpenAlex API + GitHub stars
    startup_scraper.py        YC Algolia API  (4,000+ companies)
    product_scraper.py        Product Hunt GraphQL + HN Show HN
    news_scraper.py           5 AI RSS feeds  (<24 hr filter)
    jobs_scraper.py           5 job boards    (<24 hr filter)
    linkedin_scraper.py       Stealth Playwright (Phase 5)
  llm/
    extractor.py     litellm fallback chain (Gemini 3.6 → Groq → OpenRouter → DeepSeek)
    prompts.py       One strict JSON-only prompt per schema type
  resolution/
    entity_resolver.py   Exact + fuzzy matching (rapidfuzz, threshold 85%)
    seed_entities.json   50 canonical AI startup names + aliases
  pipeline/
    orchestrator.py   5-stage pipeline — python -m src.pipeline.orchestrator
  output/
    sheets_writer.py  Batch Sheets writer (500 rows/batch, 429 backoff)
  utils/
    date_parser.py    dateparser → ISO-8601, seen-URL dedup cache
    rate_limiter.py   Async token-bucket rate limiter
  config.py           All secrets from .env, get_sheets_client()
raw/    ← Scraped JSON (gitignored, regenerable)
logs/   ← pipeline.jsonl structured log (gitignored)
```

### Production Architecture Considerations

1. **Scale Strategy (500k+ Records):**
   - **Scraper Parallelism:** Independent `asyncio.Semaphore` rate limits per domain.
   - **Deduplication:** Hash-based URL seen cache with fallback to latest non-empty dataset runs.
   - **Airflow/Cron Orchestration:** Scrapes bulk datasets weekly, fresh news/jobs every 4-12 hours.

2. **Error Resilience (413 & 429 Handling):**
   - **413 Payload Too Large:** Smart text truncation (`_smart_truncate`) caps input at ~6,000 tokens (~24,000 chars) retaining key signal paragraphs.
   - **429 Rate Limits:** Multi-tier retry using `tenacity` exponential backoff with jitter, plus automatic LLM fallback (Gemini → Groq → OpenRouter → DeepSeek).

3. **Storage & Graph Integration:**
   - **Primary DB:** PostgreSQL with JSONB schema for canonical records.
   - **Graph DB:** Neo4j integration mapping `(STARTUP)-[:LAUNCHED]->(PRODUCT)` and `(STARTUP)-[:PUBLISHED]->(RESEARCH_PAPER)`.

---

## Setup Guide

### 1. Prerequisites
- Python 3.11+
- Git

### 2. Install Dependencies

```powershell
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate      # On Windows
source .venv/bin/activate   # On Linux/macOS

# Install required packages & Playwright browser binaries
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure Environment Variables

Create `.env` from `.env.example`:

```powershell
copy .env.example .env    # On Windows
cp .env.example .env      # On Linux/macOS
```

Fill in your configuration in `.env`:

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Primary LLM — get from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GROQ_API_KEY` | ✅ | 2nd fallback — [console.groq.com/keys](https://console.groq.com/keys) |
| `OPENROUTER_API_KEY` | ✅ | 3rd fallback — [openrouter.ai/keys](https://openrouter.ai/keys) |
| `DEEPSEEK_API_KEY` | Optional | 4th fallback |
| `GOOGLE_SHEET_ID` | ✅ | Sheet ID from Google Sheet URL |
| `GOOGLE_AUTH_METHOD` | ✅ | `oauth` (default) or `service_account` |
| `GOOGLE_CREDENTIALS_JSON` | ✅ | Path to OAuth `client_secret_*.json` |
| `GITHUB_TOKEN` | Recommended | Increases rate limit to 5,000 req/hr |
| `OPENALEX_EMAIL` | Recommended | Unlocks 10 req/s polite pool |
| `PRODUCT_HUNT_TOKEN` | Optional | Product Hunt GraphQL API token |

### 4. Google Sheets Authentication (One-Time Setup)

Run the authentication setup script directly in your terminal:

```powershell
.venv\Scripts\python.exe -c "from src.config import get_sheets_client; get_sheets_client()"
```

- A browser tab will open automatically.
- Log in with the Google account that owns your Google Sheet and click **Allow**.
- Credentials will be securely saved to `credentials/token.json`.

---

## Data Schemas

Every record stored in `raw/` and written to Google Sheets follows one of these schemas.
All fields map 1-to-1 with the Google Sheets column headers.

### Startup Entity

| Field | Type | Description |
|---|---|---|
| `schemaVersion` | String | Schema version, e.g. `"1.0"` |
| `recordType` | String | Fixed: `"STARTUP"` |
| `source.name` | String | Name of the source site (e.g. `"Y Combinator"`) |
| `source.url` | String | Original source URL — every record is traceable |
| `content.entityName` | String | Canonical startup name |
| `content.data.employeeCount` | Integer | Number of employees (if available) |
| `collectedAt` | Timestamp | ISO-8601 collection time |

### Product Entity

| Field | Type | Description |
|---|---|---|
| `schemaVersion` | String | Schema version, e.g. `"1.0"` |
| `recordType` | String | Fixed: `"PRODUCT"` |
| `source.name` | String | Name of the source site |
| `source.url` | String | Original source URL |
| `content.startupName` | String | Canonical startup/maker name |
| `content.pricingModel` | Enum | `FREE` \| `FREEMIUM` \| `PAID` \| `ENTERPRISE` |
| `collectedAt` | Timestamp | ISO-8601 collection time |

### Research Paper Entity

| Field | Type | Description |
|---|---|---|
| `schemaVersion` | String | Schema version, e.g. `"1.0"` |
| `recordType` | String | Fixed: `"RESEARCH_PAPER"` |
| `content.title` | String | Title of the research paper |
| `content.authors` | Array | List of author name strings |
| `content.paper_url` | String | Link to the ArXiv / DOI page |
| `content.github_url` | String | Associated code repository URL (if any) |
| `content.github_stars` | Integer | Current GitHub star count |
| `content.published_date` | Timestamp | ISO-8601 publication date |

### Job Entity

| Field | Type | Description |
|---|---|---|
| `schemaVersion` | String | Schema version, e.g. `"1.0"` |
| `recordType` | String | Fixed: `"JOB"` |
| `content.company` | String | Canonical company name |
| `content.date` | Timestamp | ISO-8601 publication date |
| `content.is_remote` | Boolean | Remote eligibility |
| `content.role_family` | String | Functional category (e.g. `"Engineering"`, `"Research"`) |

### News Entity

| Field | Type | Description |
|---|---|---|
| `schemaVersion` | String | Schema version, e.g. `"1.0"` |
| `recordType` | String | Fixed: `"NEWS"` |
| `content.title` | String | Article headline |
| `content.summary` | String | First 300 chars of body / LLM-extracted summary |
| `content.publishedAt` | Timestamp | ISO-8601 publication date |
| `content.url` | String | Original article URL |
| `content.entities_mentioned` | Array | AI companies/products mentioned |

> **Rule:** No hallucinated data, ever. Every record traces back to a real `source.url`.

---

## Running the Pipeline

```powershell
# Set UTF-8 encoding on Windows to prevent console logging issues
$env:PYTHONIOENCODING="utf-8"

# Run full pipeline (scrape → LLM extract → resolve → Sheets)
python -m src.pipeline.orchestrator

# Or run individual scrapers as needed:
python -m src.scrapers.arxiv_scraper
python -m src.scrapers.paperswithcode_scraper
python -m src.scrapers.startup_scraper
python -m src.scrapers.product_scraper
python -m src.scrapers.news_scraper
python -m src.scrapers.jobs_scraper
```

Raw JSON files are saved in `raw/<category>/` as timestamped files.
Structured execution logs are saved in `logs/pipeline.jsonl`.

---

## Google Sheets Tabs

| Tab | Source | Rows (Typical Run) |
|---|---|---|
| Research Papers | ArXiv API + OpenAlex | 2,000+ |
| Startups | YC Algolia API | 1,000+ |
| Products | HN Show HN + Product Hunt | 997+ |
| Jobs | RemoteOK + WWR + Arbeitnow + Jobspresso | 250+ |
| News | MIT Tech Review + Verge + TechCrunch + VentureBeat | 10–50 |
| Entity Mapping Log | Raw → Canonical Entity Resolutions | 4,000+ |

---

## Anti-Bot Strategy

### LinkedIn Stealth Scraper (`linkedin_scraper.py`)

| Technique | Effect |
|---|---|
| Rotating User-Agent strings | Defeats basic UA fingerprinting |
| Randomised delays (1.5–3.5s) | Avoids request rate pattern detection |
| Realistic viewports (1366x768, 1920x1080) | Mimics desktop browsing sessions |
| `navigator.webdriver = undefined` override | Defeats Playwright/Selenium detection |
| Cookie consent auto-accept | Bypasses blocking banners |

---

## LLM Fallback Chain

| Priority | Provider | Model | Status |
|---|---|---|---|
| 1 | Gemini | `gemini-3.6-flash` | Primary — fastest |
| 2 | Groq | `llama3-8b-8192` | Free tier fallback |
| 3 | OpenRouter | `meta-llama/llama-3.1-70b-instruct` | High-quality fallback |
| 4 | DeepSeek | `deepseek-chat` | Secondary fallback |

Retry Policy: Exponential backoff with jitter on HTTP 429 rate limit errors (via `tenacity`), with immediate failover to the next provider for other status codes.
