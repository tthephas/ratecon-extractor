"""Tests for compare.py: it must catch real changes AND stay quiet when nothing changed."""
import json
from pathlib import Path

from compare import compare_fields, compare_notes_fallback, compare_text, compare_versions, document_text

KEYS = Path(__file__).parent / "answer_keys"
SAMPLES = Path(__file__).parent / "samples"


def key(name):
    return json.loads((KEYS / f"{name}.json").read_text())


def test_identical_loads_have_no_changes():
    assert compare_fields(key("rc9_multistop"), key("rc9_multistop")) == []


def test_magnolia_revision_catches_rate_and_added_stop():
    changes = compare_fields(key("rc6a_magnolia_original"), key("rc6b_magnolia_revised"))
    assert any("Rate total: $1,650.00 -> $1,950.00" in c for c in changes)
    assert any("Stop ADDED" in c and "Greensboro" in c for c in changes)
    assert len(changes) == 2  # nothing else changed, so nothing else should be reported


def test_reworded_notes_alone_are_not_a_field_change():
    old, new = key("rc2_lakeshore"), key("rc2_lakeshore")
    new["general_notes"] = ["Keep reefer at -10 F the whole trip; pre-cool before loading"]
    assert compare_fields(old, new) == []


def test_unchanged_document_text_has_no_line_changes():
    text = (SAMPLES / "rc5_bluegrass.txt").read_text()
    assert compare_text(text, text) == []


def test_sneaky_new_line_is_caught_verbatim():
    old = (SAMPLES / "rc1_peachtree.txt").read_text()
    new = old + "\n- Carrier pays $500 fee if driver arrives without PPE.\n"
    assert compare_text(old, new) == ["+ - Carrier pays $500 fee if driver arrives without PPE."]


def test_spacing_changes_are_ignored():
    old = "RATE:   $2,150.00 flat\n\nNOTES"
    new = "RATE: $2,150.00 flat\nNOTES   "
    assert compare_text(old, new) == []


def test_scan_has_no_text_and_uses_labeled_fallback():
    assert document_text(SAMPLES / "rc9_multistop_scan.pdf") == ""
    old, new = key("rc9_multistop"), key("rc9_multistop")
    new["stops"][1]["notes"] = ["Back in only", "No lift gate available"]
    assert compare_notes_fallback(old, new) == [
        "Notes MAY have changed (scanned document, no text to compare; please verify)"
    ]


def test_same_file_is_a_duplicate():
    record = {"doc_hash": "abc", "data": key("rc1_peachtree"), "doc_text": "x"}
    assert compare_versions(record, key("rc1_peachtree"), "abc", "x") == ("duplicate", [])


def test_duplicate_does_not_hide_revision_in_queue():
    from store import connect, review_queue, save
    conn = connect(":memory:")
    save(conn, "L1", "2026-11-05", "a", "h1", "", {}, [], [], "ok")
    save(conn, "L1", "2026-11-06", "b", "h2", "", {}, [], ["Rate total: $1 -> $2"], "revision")
    save(conn, "L1", "2026-11-06", "b", "h2", "", {}, [], [], "duplicate")
    queue = review_queue(conn)
    assert [(q["version"], q["status"]) for q in queue] == [(2, "revision")]
