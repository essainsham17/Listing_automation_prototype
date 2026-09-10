# Listing Automation Prototype

A prototype that turns a new car from the Salesforce stock report into a filled listing form. It finds the car's spec-sheet and photo folder, extracts the spec sheet with an LLM, matches the result to the form's fields and feature checkboxes, and sorts the photos into exterior and interior for a person to review and publish.

## Repositories

The prototype is split into two repositories. This README is the same in both.

| Repository | What it holds |
|---|---|
| [Listing_automation_prototype](https://github.com/essainsham17/Listing_automation_prototype) | Backend: the FastAPI API, prototype data and the Docker setup |
| [lisitng_automation_frontend](https://github.com/essainsham17/lisitng_automation_frontend) | Frontend: the admin panel pages |

The pages call the API on the same address they are loaded from, so the backend serves them. Clone both repositories side by side and point the backend at the frontend with `FRONTEND_DIR`. Without a frontend folder the backend runs as an API only.

## Backend repository

| Path | What it holds |
|---|---|
| `app/` | FastAPI application (`app.main:app`): extraction, matching, folder lookup, Stock Sync and photo classification |
| `data/` | Prototype data: listings, pending stock report, folder index, the form's `schema.json` and `taxonomy.json`, reference catalogs |
| `scripts/` | `crawl_folder_index.py`, which rebuilds `data/folder_index.json` from the photo library |
| `requirements.txt` | Python dependencies |
| `Dockerfile`, `docker-compose.yml` | Container setup |

The backend also serves `/schema.json` and `/taxonomy.json` from its `data/` folder.

## Frontend repository

| File | What it is |
|---|---|
| `index.html` | Add Car Details wizard page: spec sheet auto-fill form, photo upload and publish steps. |
| `app.js` | Front end of the new-listing wizard: renders the form, auto-fills, AI-matches and publishes. |
| `styles.css` | Shared admin panel styles: sidebar shell, listing wizard, form fields, photos and warnings. |
| `stock-sync.html` | Stock Sync admin page that compares a stock file against live listings and applies the changes. |
| `website.html` | Car Inventory page that fetches all listings from /inventory and shows them as cards. |

## Requirements

- Python 3.14
- Poppler (`pdftoppm` and `pdftotext` on PATH)
- The spec-sheet and photo library on the local machine

## Setup

```bash
git clone https://github.com/essainsham17/Listing_automation_prototype.git
git clone https://github.com/essainsham17/lisitng_automation_frontend.git
cd Listing_automation_prototype
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

On macOS or Linux, activate with `source venv/bin/activate`.

## Environment variables

Create a file named `.env` in the backend repository folder and add the keys below. The `.env` file is git-ignored: never commit it and never share its real values.

| Key | Needed? | What to put in it |
|---|---|---|
| `EDENAI_API_KEY` | **Required** | Your Eden AI API key. The prototype uses Eden AI to read spec sheets and match them to the form. |
| `LOCAL_PHOTOS_ROOT` | **Required** | Full path to the folder that holds the car spec sheets and photos. Auto-fill returns 503 if this folder does not exist. |
| `FRONTEND_DIR` | Recommended | Path to your clone of the frontend repository. Relative paths are resolved from the backend folder. When unset, the backend looks for `../frontend` and then `../lisitng_automation_frontend`, and serves the API only if neither exists. |
| `EDENAI_BASE_URL` | Optional | Eden AI endpoint. Default: `https://api.edenai.run/v3` |
| `EDENAI_MODEL` | Optional | Model used to read spec sheets. Default: `google/gemma-4-31b-it` |
| `GROQ_API_KEY` | Optional | Groq API key, used for matching only when no Eden AI or Anthropic key is set. |
| `TAVILY_API_KEY` | Optional | Tavily web-search API key, read by `app/web_search.py`. |
| `LANGFUSE_PUBLIC_KEY` | Optional | Langfuse public key, for tracing AI calls. Tracing stays off unless both Langfuse keys are set. |
| `LANGFUSE_SECRET_KEY` | Optional | Langfuse secret key. |
| `LANGFUSE_HOST` | Optional | Langfuse server URL, for example `https://cloud.langfuse.com` |
| `EXTRACTION_ENABLED` | Optional | `true` or `false`. `false` skips reading the spec sheet during auto-fill. Default: `true` |
| `PHOTO_CLASSIFICATION_ENABLED` | Optional | `true` or `false`. `false` skips sorting photos into exterior and interior. Default: `true` |

Example `.env`, with placeholders to replace by your own values:

```
EDENAI_API_KEY=your-eden-ai-api-key
LOCAL_PHOTOS_ROOT=/path/to/spec-sheet-and-photo-library
FRONTEND_DIR=../lisitng_automation_frontend
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

Every other setting (timeouts, limits, other AI providers) has a working default in `app/config.py`.

## Run

From the backend repository folder:

```bash
python -m uvicorn app.main:app --reload --port 4000
```

Then open http://localhost:4000.

## Docker

Docker files live in the backend repository only. From the backend repository folder:

```bash
PHOTOS_LIBRARY=/path/to/photo/library FRONTEND_PATH=../lisitng_automation_frontend docker compose up --build
```

The image is based on `python:3.14-slim` with Poppler. Compose reads `.env`, mounts the photo library read-only at `/library` and the frontend checkout at `/srv/frontend`, keeps `data/` on the host and serves the app on port 4000. `FRONTEND_PATH` defaults to `../lisitng_automation_frontend`.
