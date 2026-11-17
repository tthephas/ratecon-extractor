"""Find what changed between two versions of the same load.

Three steps, cheapest and most reliable first:
  1. Fingerprint: identical files are duplicates. No comparison needed.
  2. Structured fields (rate, stops, dates, references) from Claude's extraction.
     These are transcriptions and stay stable from run to run.
  3. Wording (notes, terms): compare the broker's ORIGINAL document line by line,
     not Claude's paraphrase, which varies from run to run. For scans with no
     text layer, fall back to Claude's notes and say so.
"""
import difflib
import hashlib
import re
from pathlib import Path

from pypdf import PdfReader

MIN_TEXT_CHARS = 50  # a PDF with less text than this is treated as a scan


# ---------- Step 1: fingerprint ----------

def fingerprint(path: Path) -> str:
    """A short ID computed from the file's exact bytes. Same file -> same fingerprint."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- Step 3 helper: the document's own text ----------

def document_text(path: Path) -> str:
    """The original words in the document, or "" for a scan with no text layer."""
    if path.suffix.lower() == ".pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        return text if len(text.strip()) >= MIN_TEXT_CHARS else ""
    return path.read_text()


# ---------- Step 2: structured fields ----------

def _money(value):
    """'1650.00', 1650 and 1650.0 all become 1650.0 so they compare equal."""
    if value is None:
        return None
    return round(float(str(value).replace(",", "").replace("$", "")), 2)


def _fmt_money(value):
    return "none" if value is None else f"${value:,.2f}"


def _stop_key(stop: dict) -> tuple:
    """Match stops between versions by type + place, not by sequence number.
    Inserting a stop in the middle renumbers everything after it, so sequence alone
    would make every later stop look 'changed'."""
    loc = stop.get("location") or {}
    return (stop.get("stop_type"), (loc.get("city") or "").lower(), (loc.get("state") or "").upper())


def _stop_label(stop: dict) -> str:
    loc = stop.get("location") or {}
    return f"{stop.get('stop_type', 'stop')} {loc.get('facility_name', '')}, {loc.get('city', '')}, {loc.get('state', '')}"


def _time_label(t: dict) -> str:
    t = t or {}
    times = "-".join(x[:5] for x in (t.get("start"), t.get("end")) if x)
    return f"{t.get('date')} {t.get('kind')} {times}".strip()


def compare_fields(old: dict, new: dict) -> list[str]:
    """Bullet-point list of meaningful field changes between two extractions."""
    changes = []
    old, new = old or {}, new or {}

    # Rate
    old_rate, new_rate = old.get("rate") or {}, new.get("rate") or {}
    for field, label in [("total", "Rate total"), ("linehaul", "Linehaul"), ("fuel_surcharge", "Fuel")]:
        a, b = _money(old_rate.get(field)), _money(new_rate.get(field))
        if a != b:
            changes.append(f"{label}: {_fmt_money(a)} -> {_fmt_money(b)}")
    for group, label in [("accessorials", "Accessorials"), ("reimbursements", "Reimbursements")]:
        a = sorted(_money(x.get("amount")) or 0 for x in old_rate.get(group) or [])
        b = sorted(_money(x.get("amount")) or 0 for x in new_rate.get(group) or [])
        if a != b:
            changes.append(f"{label} changed: {a} -> {b}")

    # Warnings with a dollar amount
    a = sorted(_money(p.get("amount")) or 0 for p in old.get("penalties") or [])
    b = sorted(_money(p.get("amount")) or 0 for p in new.get("penalties") or [])
    if a != b:
        changes.append(f"Fine/penalty amounts changed: {a} -> {b}")

    # Simple top-level fields
    for field, label in [("equipment", "Equipment"), ("tracking_required", "Tracking required")]:
        if old.get(field) != new.get(field):
            changes.append(f"{label}: {old.get(field)} -> {new.get(field)}")
    for field in ["weight_lbs", "pallet_count"]:
        a = (old.get("commodity") or {}).get(field)
        b = (new.get("commodity") or {}).get(field)
        if a != b:
            changes.append(f"Commodity {field}: {a} -> {b}")

    # Stops: added, removed, or changed
    old_stops = {_stop_key(s): s for s in old.get("stops") or []}
    new_stops = {_stop_key(s): s for s in new.get("stops") or []}
    for key, stop in new_stops.items():
        if key not in old_stops:
            changes.append(f"Stop ADDED: {_stop_label(stop)} ({_time_label(stop.get('time'))})")
    for key, stop in old_stops.items():
        if key not in new_stops:
            changes.append(f"Stop REMOVED: {_stop_label(stop)}")
    for key in old_stops.keys() & new_stops.keys():
        a, b = old_stops[key], new_stops[key]
        if _time_label(a.get("time")) != _time_label(b.get("time")):
            changes.append(f"Time changed at {_stop_label(b)}: {_time_label(a.get('time'))} -> {_time_label(b.get('time'))}")
        refs_a = sorted(r.get("value") for r in a.get("references") or [])
        refs_b = sorted(r.get("value") for r in b.get("references") or [])
        if refs_a != refs_b:
            changes.append(f"References changed at {_stop_label(b)}: {refs_a} -> {refs_b}")

    return changes


# ---------- Step 3: wording ----------

def _clean_lines(text: str) -> list[str]:
    """Collapse extra spaces and drop blank lines, so layout changes don't count."""
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return [line for line in lines if line]


def compare_text(old_text: str, new_text: str) -> list[str]:
    """Lines removed (-) or added (+) between the two original documents, verbatim."""
    diff = difflib.ndiff(_clean_lines(old_text), _clean_lines(new_text))
    return [line for line in diff if line.startswith(("+ ", "- "))]


def compare_notes_fallback(old: dict, new: dict) -> list[str]:
    """For scans only: compare Claude's notes. Less reliable, so it's labeled as such."""
    def notes(d):
        found = list(d.get("general_notes") or [])
        for s in d.get("stops") or []:
            found += s.get("notes") or []
        return sorted(n.strip().lower() for n in found)
    if notes(old or {}) != notes(new or {}):
        return ["Notes MAY have changed (scanned document, no text to compare; please verify)"]
    return []


# ---------- Put it together ----------

def compare_versions(old_record: dict, new_data: dict, new_hash: str, new_text: str) -> tuple[str, list[str]]:
    """Compare a stored version with a newly received one.

    Returns (kind, changes): kind is "duplicate", "revision" or "no_change".
    """
    if old_record["doc_hash"] == new_hash:
        return "duplicate", []

    changes = compare_fields(old_record["data"], new_data)
    old_text = old_record.get("doc_text") or ""
    if old_text and new_text:
        line_changes = compare_text(old_text, new_text)
        if line_changes:
            changes.append("Document lines changed:")
            changes += [f"  {line}" for line in line_changes]
    else:
        changes += compare_notes_fallback(old_record["data"], new_data)

    return ("revision" if changes else "no_change"), changes
