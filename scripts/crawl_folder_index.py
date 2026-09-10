"""Crawls the local photo library and saves the folder index to data/folder_index.json."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.folder_index import build_index, save_index

if __name__ == "__main__":
    index = build_index(config.LOCAL_PHOTOS_ROOT)
    save_index(index)
    total_pdfs = sum(len(leaf["pdf_names"]) for leaf in index)
    print(f"Wrote data/folder_index.json — {len(index)} leaf folder(s), {total_pdfs} PDF(s) total, "
          f"crawled from {config.LOCAL_PHOTOS_ROOT}")
