"""Compares listings against an uploaded Salesforce stock export and stages and applies the changes."""

import json
import logging
import re
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

from app.db import load_listings, now_iso, save_listings

REQUIRED_COLUMNS = {"model_code", "aed_price"}

COLUMN_ALIASES = {
    "list_price": "aed_price",
    "description": "product_description",
    "int_color": "interior_color",
    "color": "exterior_color",
    "vehicle_card:_vehicle_card_name": "stock_id",
}
PENDING_PATH = Path(__file__).parent.parent / "data" / "pending_stock_report.json"

REPORT_BUCKETS = ("new", "price_updated", "hidden", "unchanged", "restocked")


def _guess_brand(text: str) -> str:
    """Returns the first hyphen-delimited token of the text, title-cased, as a best-effort brand guess."""
    if not text:
        return ""
    return str(text).split("-")[0].strip().title()


def _guess_year(model_code: str) -> int | None:
    """Returns 2000 plus a model code's trailing hyphenated two-digit year, or None if it has none."""
    match = re.search(r"-(\d{2})$", str(model_code).strip())
    return 2000 + int(match.group(1)) if match else None


def _read_table(file_path: str):
    """Reads the stock file with read_excel, falling back to the largest HTML table when it is not real Excel."""
    try:
        return pd.read_excel(file_path)
    except Exception as excel_error:
        try:
            tables = pd.read_html(file_path)
        except Exception:
            raise excel_error
        if not tables:
            raise excel_error
        logger.info("stock_sync: %s is an HTML table, not a real Excel file — read it as HTML",
                    file_path)
        return max(tables, key=len)


def _price(row) -> float | None:
    """Returns the row's aed_price as a float, or None when it is missing, blank, NaN or not numeric."""
    value = row.get("aed_price")
    if value is None or (isinstance(value, float) and value != value):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _key(value) -> str:
    """Returns an identifier normalised for matching: single-spaced, upper-case, and empty for None or NaN."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    text = " ".join(str(value).split()).upper()
    return "" if text == "NAN" else text


def parse_excel(file_path: str) -> dict[str, dict]:
    """Reads the stock file into one row per Model Code, keeping the first priced row and every Description and Stock ID seen."""
    df = _read_table(file_path)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns={old: new for old, new in COLUMN_ALIASES.items()
                            if old in df.columns and new not in df.columns})
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"Excel is missing required column(s): {sorted(missing)}. "
            f"Found columns: {list(df.columns)}"
        )

    rows: dict[str, dict] = {}
    collapsed = 0
    for _, row in df.iterrows():
        code = str(row["model_code"]).strip()
        if not code or code.lower() == "nan":
            continue
        entry = row.to_dict()
        aliases = {_key(entry.get("product_description")), _key(entry.get("stock_id"))} - {""}
        existing = rows.get(code)
        if existing is None:
            entry["_aliases"] = sorted(aliases)
            rows[code] = entry
            continue
        collapsed += 1
        aliases.update(existing["_aliases"])
        if _price(existing) is None and _price(entry) is not None:
            rows[code] = entry
        rows[code]["_aliases"] = sorted(aliases)
    if collapsed:
        logger.info("stock_sync: %d row(s) collapsed into %d model code(s) — a Model Code covers "
                    "several physical cars", collapsed, len(rows))
    return rows


def compute_report(excel_path: str) -> dict:
    """Compares the stock file with the listings, matching by Model Code, Description or Stock ID, and returns buckets without writing."""
    excel_rows = parse_excel(excel_path)
    listings = load_listings()

    code_for = {alias: code for code, row in excel_rows.items() for alias in row.get("_aliases", [])}
    code_for.update({_key(code): code for code in excel_rows})
    db_by_model = {}
    for listing in listings:
        code = code_for.get(_key(listing["model_number"]))
        if code is not None:
            db_by_model.setdefault(code, listing)

    report = {"new": [], "price_updated": [], "hidden": [], "unchanged": [], "restocked": []}

    for model_number, row in excel_rows.items():
        new_price = _price(row)

        if model_number not in db_by_model:
            description = str(row.get("product_description") or "")
            report["new"].append({
                "model_number": model_number,
                "product_description": description,
                "brand": _guess_brand(description) or _guess_brand(model_number),
                "model": "", "trim": "",
                "year": _guess_year(model_number) or "",
                "price_aed": new_price,
                "available": row.get("available"),
                "exterior_color": str(row.get("exterior_color") or "") or None,
                "interior_color": str(row.get("interior_color") or "") or None,
            })
            continue

        listing = db_by_model[model_number]
        was_hidden = listing.get("status") == "hidden"
        old_price = listing.get("price_aed")

        if new_price is None:
            report["unchanged"].append({"model_number": listing["model_number"], "price_aed": old_price})
            continue

        if was_hidden:
            report["restocked"].append({
                "model_number": listing["model_number"], "old_price_aed": old_price,
                "new_price_aed": new_price,
                "product_description": str(row.get("product_description") or ""),
            })
        elif old_price != new_price:
            report["price_updated"].append({
                "model_number": listing["model_number"], "old_price_aed": old_price,
                "new_price_aed": new_price,
                "product_description": str(row.get("product_description") or ""),
            })
        else:
            report["unchanged"].append({"model_number": listing["model_number"], "price_aed": old_price})

    for listing in listings:
        if _key(listing["model_number"]) not in code_for and listing.get("status") == "active":
            report["hidden"].append({"model_number": listing["model_number"], "price_aed": listing.get("price_aed"),
                                     "product_description": ""})

    return report


def apply_hidden(model_numbers: list[str]) -> list[str]:
    """Marks the given listings hidden if not already, saves the DB, and returns the model numbers changed."""
    listings = load_listings()
    applied = []
    wanted = set(model_numbers)
    for listing in listings:
        if listing["model_number"] in wanted and listing.get("status") != "hidden":
            listing["status"] = "hidden"
            listing["updated_at"] = now_iso()
            applied.append(listing["model_number"])
    save_listings(listings)
    return applied


def apply_price_updates(items: list[dict]) -> list[str]:
    """Sets new prices on the given listings, marks them active, saves the DB, and returns the model numbers."""
    by_model = {item["model_number"]: item["new_price_aed"] for item in items}
    listings = load_listings()
    applied = []
    for listing in listings:
        if listing["model_number"] in by_model:
            listing["price_aed"] = float(by_model[listing["model_number"]])
            listing["status"] = "active"
            listing["updated_at"] = now_iso()
            applied.append(listing["model_number"])
    save_listings(listings)
    return applied


def save_pending_report(report: dict, excel_filename: str) -> dict:
    """Saves the report buckets with upload time and filename as the pending report and returns that payload."""
    payload = {"uploaded_at": now_iso(), "excel_filename": excel_filename,
               **{bucket: report.get(bucket, []) for bucket in REPORT_BUCKETS}}
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def load_pending_report() -> dict | None:
    """Returns the saved pending stock report, or None if no pending report file exists."""
    if not PENDING_PATH.exists():
        return None
    return json.loads(PENDING_PATH.read_text())


def clear_pending_report() -> None:
    """Deletes the pending stock report file if it exists."""
    if PENDING_PATH.exists():
        PENDING_PATH.unlink()


def get_pending_new_entry(model_number: str) -> dict | None:
    """Returns the staged entry for a model number from the pending report's new bucket, or None."""
    report = load_pending_report()
    if not report:
        return None
    for entry in report.get("new", []):
        if entry.get("model_number") == model_number:
            return entry
    return None


def get_pending_new_description(model_number: str) -> str | None:
    """Returns the product description from a model number's staged new entry, or None if unavailable."""
    entry = get_pending_new_entry(model_number)
    return (entry or {}).get("product_description") or None


def remove_from_pending(bucket: str, model_numbers: list[str]) -> dict | None:
    """Removes the given model numbers from one bucket of the pending report, re-saves it, and returns it or None."""
    report = load_pending_report()
    if report is None:
        return None
    wanted = set(model_numbers)
    report[bucket] = [item for item in report.get(bucket, []) if item["model_number"] not in wanted]
    PENDING_PATH.write_text(json.dumps(report, indent=2))
    return report


def _entry_keys(entry: dict) -> set[str]:
    """Returns the normalised Model Code and Product Description of one pending entry."""
    return {_key(entry.get("model_number")), _key(entry.get("product_description"))} - {""}


def remove_published_new(identifier: str) -> dict | None:
    """Removes a just-published car from the pending New Models, matched by Model Code or Product Description."""
    report = load_pending_report()
    if report is None:
        return None
    key = _key(identifier)
    report["new"] = [e for e in report.get("new", []) if key not in _entry_keys(e)]
    PENDING_PATH.write_text(json.dumps(report, indent=2))
    return report


def reconcile_with_inventory(report: dict, listings: list[dict]) -> dict:
    """Hides pending rows the inventory already settles: listed cars from New Models and in-stock cars from Sold Out."""
    listed = {_key(l.get("model_number")) for l in listings} - {""}
    in_stock = set().union(*(_entry_keys(e) for e in report.get("new", [])))
    out = dict(report)
    out["new"] = [e for e in report.get("new", []) if not (_entry_keys(e) & listed)]
    out["hidden"] = [h for h in report.get("hidden", []) if _key(h.get("model_number")) not in in_stock]
    return out
