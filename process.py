"""Process incoming rate cons the way a carrier's system would: extract, compare to
any earlier version of the same load, store, and show what needs a person.

    python3 process.py samples/rc6a_magnolia_original.txt samples/rc6b_magnolia_revised.txt
    python3 process.py --queue

This is separate from extract.py on purpose: the eval runs every sample, including
format variants of the same load (rc1 as .txt AND .pdf), which here would look like
revisions. The eval measures accuracy; this script simulates real arrivals.
"""
import json
import sys
from datetime import date
from pathlib import Path

from compare import compare_versions, document_text, fingerprint
from extract import base_name, build_content, extract
from store import connect, latest_version, review_queue, save


def process(conn, path: Path, received: date):
    doc_hash = fingerprint(path)
    doc_text = document_text(path)
    result = extract(build_content(path), received)
    data, errors = result["raw"], result["errors"]

    # If Claude couldn't read a load number, give it a unique placeholder so
    # unreadable documents don't get mistaken for versions of each other.
    load_number = (data or {}).get("broker_load_number") or f"UNKNOWN-{doc_hash[:8]}"

    previous = latest_version(conn, load_number)
    if previous is None:
        kind, changes = "new", []
    else:
        kind, changes = compare_versions(previous, data, doc_hash, doc_text)

    if kind == "duplicate":
        status = "duplicate"
    elif errors:
        status = "needs_review"
    elif kind == "revision":
        status = "revision"
    else:
        status = "ok"

    version = save(conn, load_number, received.isoformat(), str(path), doc_hash, doc_text,
                   data, errors, changes, status)

    broker = (data or {}).get("broker_name", "unknown broker")
    print(f"\n{status.upper()}: {load_number} ({broker}), version {version}")
    for item in errors:
        print(f"  ! {item}")
    for item in changes:
        print(f"  • {item}" if not item.startswith("  ") else f"    {item.strip()}")


def show_queue(conn):
    queue = review_queue(conn)
    print(f"\nREVIEW QUEUE: {len(queue)} load(s) need a person")
    for row in queue:
        summary = json.loads(row["errors"]) + json.loads(row["changes"])
        first = summary[0] if summary else ""
        print(f"  {row['status']:13} {row['load_number']:18} v{row['version']}  {first}")


def main():
    conn = connect()
    if sys.argv[1:] == ["--queue"]:
        show_queue(conn)
        return
    dates_file = Path("samples/received_dates.json")
    received_dates = json.loads(dates_file.read_text()) if dates_file.exists() else {}
    for arg in sys.argv[1:]:
        path = Path(arg)
        received = received_dates.get(base_name(path.stem), date.today().isoformat())
        process(conn, path, date.fromisoformat(received))


if __name__ == "__main__":
    main()
