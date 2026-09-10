# Listing Automation Prototype

A prototype that turns a new car from the Salesforce stock report into a filled listing form. It finds the car's spec-sheet and photo folder, extracts the spec sheet with an LLM, matches the result to the form's fields and feature checkboxes, and sorts the photos into exterior and interior for a person to review and publish.

## Layout

| Path | What it holds |
|---|---|
| `frontend/` | Admin panel pages: `index.html` (Add Car Details), `stock-sync.html`, `marketing-tracker.html`, `website.html`, with `app.js` and `styles.css` |
| `backend/app/` | FastAPI application (`app.main:app`): extraction, matching, folder lookup, Stock Sync, marketing tracker and photo classification |
| `backend/data/` | Prototype data: listings, pending stock report, marketing tracker, folder index, the form's `schema.json` and `taxonomy.json`, reference catalogs |
| `backend/scripts/` | `crawl_folder_index.py`, which rebuilds `data/folder_index.json` from the photo library |

The backend serves the `frontend/` pages and serves `/schema.json` and `/taxonomy.json` from `backend/data/`.

## Requirements

- Python 3.14
- Poppler (`pdftoppm` and `pdftotext` on PATH)
- The spec-sheet and photo library on the local machine

## Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

On macOS or Linux, activate with `source venv/bin/activate`.

## Environment variables

Create a file named `.env` inside the `backend/` folder and add the keys below. The `.env` file is git-ignored: never commit it and never share its real values.

| Key | Needed? | What to put in it |
|---|---|---|
| `EDENAI_API_KEY` | **Required** | Your Eden AI API key. The prototype uses Eden AI to read spec sheets and match them to the form. |
| `LOCAL_PHOTOS_ROOT` | **Required** | Full path to the folder that holds the car spec sheets and photos. Auto-fill returns 503 if this folder does not exist. |
| `EDENAI_BASE_URL` | Optional | Eden AI endpoint. Default: `https://api.edenai.run/v3` |
| `EDENAI_MODEL` | Optional | Model used to read spec sheets. Default: `google/gemma-4-31b-it` |
| `GROQ_API_KEY` | Optional | Groq API key, used for matching only when no Eden AI or Anthropic key is set. |
| `TAVILY_API_KEY` | Optional | Tavily web-search API key, read by `app/web_search.py`. |
| `LANGFUSE_PUBLIC_KEY` | Optional | Langfuse public key, for tracing AI calls. Tracing stays off unless both Langfuse keys are set. |
| `LANGFUSE_SECRET_KEY` | Optional | Langfuse secret key. |
| `LANGFUSE_HOST` | Optional | Langfuse server URL, for example `https://cloud.langfuse.com` |
| `EXTRACTION_ENABLED` | Optional | `true` or `false`. `false` skips reading the spec sheet during auto-fill. Default: `true` |
| `PHOTO_CLASSIFICATION_ENABLED` | Optional | `true` or `false`. `false` skips sorting photos into exterior and interior. Default: `true` |

Example `backend/.env`, with placeholders to replace by your own values:

```
EDENAI_API_KEY=your-eden-ai-api-key
LOCAL_PHOTOS_ROOT=/path/to/spec-sheet-and-photo-library
EDENAI_BASE_URL=https://api.edenai.run/v3
EDENAI_MODEL=google/gemma-4-31b-it
GROQ_API_KEY=
TAVILY_API_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
EXTRACTION_ENABLED=true
PHOTO_CLASSIFICATION_ENABLED=true
```

Every other setting (timeouts, limits, other AI providers) has a working default in `backend/app/config.py`.

## Run

```bash
cd backend
python -m uvicorn app.main:app --reload --port 4000
```

From the repository root, add `--app-dir backend` to the same command. Then open http://localhost:4000.

## Docker

```bash
PHOTOS_LIBRARY=/path/to/photo/library docker compose up --build
```

The image is based on `python:3.14-slim` with Poppler. Compose reads `backend/.env`, mounts the photo library read-only at `/library`, keeps `backend/data` on the host and serves the app on port 4000.
