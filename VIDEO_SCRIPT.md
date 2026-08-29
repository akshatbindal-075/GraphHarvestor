# GraphHarvester — 5-Minute Video Demo Script & Visual Blueprint

> **Target Duration:** 5 Minutes (300 seconds)  
> **Format:** Screen Capture + Voiceover (Voice + Webcam in corner optional)  
> **Key Goal:** Explain GraphHarvester's architecture, resilience, scraping mechanics, LLM extraction chain, entity resolution, and live output in Google Sheets.

---

## 🕒 Executive Timeline Summary

| Time | Segment | Main Focus | On-Screen Visual |
|---|---|---|---|
| **0:00 – 1:00** | **1. Hook & Problem** | Manual data collection vs. Automated Ingestion | GitHub Repo & Architecture Diagram |
| **1:00 – 2:00** | **2. Scraping Engine** | 6 Async Sources, Algolia API, Playwright Stealth | Code (`src/scrapers/`) + Scraper Runs |
| **2:00 – 3:00** | **3. LLM Fallback & Resilience** | Gemini 3.6 → Groq → OpenRouter → DeepSeek, 429 Backoff | Code (`src/llm/extractor.py`) & Terminal Logs |
| **3:00 – 4:00** | **4. Entity Resolution** | RapidFuzz fuzzy matching @ 85%, Seed List, Graph DB | `src/resolution/entity_resolver.py` |
| **4:00 – 5:00** | **5. Live Execution & Output** | Real-time orchestrator run & 8,000+ Google Sheets rows | Terminal Run + Live Google Sheets Tabs |

---

## 🎬 Detailed Script & Visual Instructions

### 🎥 Segment 1: The Hook & System Architecture (0:00 – 1:00)

**[VISUAL ON SCREEN]**
- **0:00–0:15:** Full-screen title slide: **GraphHarvester — Production-Grade AI Data Pipeline**.
- **0:15–0:40:** Switch to VS Code showing `README.md` with the ASCII Architecture Diagram centered on screen.
- **0:40–1:00:** Highlight `architecture.pdf` on screen.

**[VOICEOVER SCRIPT]**
> *"Welcome! In the fast-moving AI landscape, tracking startups, research breakthroughs, launches, jobs, and news requires constantly ingesting thousands of records daily. Doing this manually is impossible — and standard scraping scripts break on anti-bot systems, rate limits, or API changes.*
>
> *Enter **GraphHarvester** — a production-grade async data ingestion pipeline built in Python 3.11+. GraphHarvester collects over 4,000 records daily across 6 specialized data categories, enriches them using a 4-tier LLM fallback chain, performs fuzzy entity resolution against seed databases, and writes structured outputs to Google Sheets and Neo4j graph databases in real time.*
>
> *Let's look under the hood at how the engine is built."*

---

### 🎥 Segment 2: Async Scraping Engine & Stealth Mechanics (1:00 – 2:00)

**[VISUAL ON SCREEN]**
- **1:00–1:30:** Open `src/scrapers/` in VS Code split-screen:
  - Left: `startup_scraper.py` (showing YC Algolia API pagination).
  - Right: `paperswithcode_scraper.py` (showing OpenAlex API + GitHub star enrichment).
- **1:30–2:00:** Open `linkedin_scraper.py` highlighting stealth flags (`navigator.webdriver = undefined`, user-agent rotation, viewport spoofing).

**[VOICEOVER SCRIPT]**
> *"GraphHarvester powers data ingestion using asynchronous `aiohttp` and Playwright Chromium.*
>
> *Instead of fragile web scraping, we prioritize high-throughput APIs. For example, our Y Combinator startup scraper hooks directly into YC's live Algolia search API, retrieving 1,000+ startups in under 3 seconds.*
>
> *For research papers, we ingest over 2,000 papers from ArXiv and OpenAlex, enriching each paper with real-time GitHub star counts.*
>
> *For anti-bot protected targets like LinkedIn, we implement stealth Playwright instances with randomized viewports, header rotation, and `navigator.webdriver` overrides to bypass bot detection mechanisms."*

---

### 🎥 Segment 3: Resilient LLM Extraction Chain (2:00 – 3:00)

**[VISUAL ON SCREEN]**
- **2:00–2:30:** Open `src/llm/extractor.py` in VS Code. Highlight `LLM_MODELS = ["gemini/gemini-3.6-flash", "groq/llama3-8b-8192", "openrouter/meta-llama/llama-3.1-70b-instruct", "deepseek/deepseek-chat"]`.
- **2:30–3:00:** Scroll to `_smart_truncate()` and `tenacity` retry decorator for HTTP 429 rate limit handling.

**[VOICEOVER SCRIPT]**
> *"Raw web data is often unstructured. To transform unstructured articles and listings into strict canonical JSON schemas, GraphHarvester utilizes `litellm` with a multi-provider fallback strategy.*
>
> *Our fallback chain starts with **Gemini 3.6 Flash** for maximum speed. If Gemini hits a rate limit or service interruption, the pipeline automatically falls back to **Groq Llama 3**, then to **OpenRouter**, and finally **DeepSeek**.*
>
> *To handle 413 Payload Errors on large documents, our `_smart_truncate()` module extracts high-density signal paragraphs. And to prevent rate-limit crashes, our `tenacity` engine applies exponential backoff with random jitter exclusively on HTTP 429 responses — failing fast on all other errors so the pipeline never deadlocks."*

---

### 🎥 Segment 4: Entity Resolution & Deduplication (3:00 – 4:00)

**[VISUAL ON SCREEN]**
- **3:00–3:30:** Open `src/resolution/entity_resolver.py` and `src/resolution/seed_entities.json`. Show match confidence thresholding (`rapidfuzz` ratio @ 85%).
- **3:30–4:00:** Open `architecture.pdf` section 4 showing the Neo4j & PostgreSQL schema integration diagram.

**[VOICEOVER SCRIPT]**
> *"Raw scraped entities often contain variation — like 'OpenAI', 'OpenAI Inc.', or 'OpenAI LLC'.*
>
> *GraphHarvester solves this in Stage 4 using our **Entity Resolver**. Powered by `rapidfuzz` string matching, it evaluates company names against a seed directory of 50 canonical AI entities and aliases. Any match above an 85% confidence score is unified automatically.*
>
> *Furthermore, our deduplication engine uses a two-tier cache (`seen_urls.json` and file modification timestamps) to guarantee no article or job listing is ever processed twice. For enterprise scaling, this architecture maps directly into PostgreSQL for JSONB storage and Neo4j for graph relationship traversals."*

---

### 🎥 Segment 5: Live Execution & Google Sheets Output (4:00 – 5:00)

**[VISUAL ON SCREEN]**
- **4:00–4:25:** Open PowerShell terminal in VS Code and run:  
  `$env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m src.pipeline.orchestrator`
- Show logs rapidly progressing through Stages 1 to 5 in ~80 seconds.
- **4:25–4:50:** Switch browser to Google Sheets:
  - Tab 1: **Research Papers** (2,000 rows with GitHub stars)
  - Tab 2: **Startups** (1,000 YC rows)
  - Tab 3: **Products** (997 rows)
  - Tab 4: **Jobs** (256 remote AI jobs)
  - Tab 5: **News** (Fresh AI news)
  - Tab 6: **Entity Mapping Log** (4,300+ match entries)
- **4:50–5:00:** Return to GitHub repository page with call to action.

**[VOICEOVER SCRIPT]**
> *"Let's see GraphHarvester in action.*
>
> *Executing the orchestrator triggers all five pipeline stages concurrently. In under 90 seconds, it scrapes the web, extracts structured schemas, resolves entities, and streams batches of 500 rows to the Google Sheets API with rate-limit protection.*
>
> *And here are the live results in Google Sheets! Over **8,600 total rows** clean, formatted, and deduplicated across 6 dedicated tabs — ready for intelligence analysis, investment tracking, or graph database ingestion.*
>
> *Thank you for watching! Check out the full source code and documentation on GitHub at `github.com/akshatbindal-075/GraphHarvestor`."*

---

## 💡 Quick Tips for Recording
1. **Screen Resolution:** Record at 1920x1080 (1080p) or 4K.
2. **VS Code Theme:** Use dark mode (e.g. GitHub Dark Default or One Dark Pro) with 14pt-16pt font size for maximum clarity.
3. **Audio:** Use a clean USB microphone or headset; apply light noise suppression in OBS / Camtasia / Loom.
