# Listing Automation Prototype

A prototype that turns a new car from the Salesforce stock report into a filled listing form. It finds the car's spec-sheet and photo folder, extracts the spec sheet with an LLM, matches the result to the form's fields and feature checkboxes, and sorts the photos into exterior and interior for a person to review and publish.

## Layout

| Path | What it holds |
|---|---|
| `frontend/` | Admin panel pages: `index.html` (Add Car Details), `stock-sync.html`, `marketing-tracker.html`, `website.html`, with `app.js` and `styles.css` |
| `backend/app/` | FastAPI application (`app.main:app`): extraction, matching, folder lookup, Stock Sync, marketing tracker and photo classification |
| `backend/data/` | Prototype data: listings, pending stock report, marketing tracker, folder index, the form's `schema.json` and `taxonomy.json`, reference catalogs |
| `backend/scripts/` | `crawl_folder_index.py`, which rebuilds `data/folder_index.json` from the photo library |
| `docs/` | Design notes and the workflow diagram |

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

Create `backend/.env` with the keys below. The file is git-ignored and must never be committed.

```
GROQ_API_KEY=
LOCAL_PHOTOS_ROOT=
TAVILY_API_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=
PHOTO_CLASSIFICATION_ENABLED=
EXTRACTION_ENABLED=
EDENAI_API_KEY=
EDENAI_BASE_URL=
EDENAI_MODEL=
```

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
