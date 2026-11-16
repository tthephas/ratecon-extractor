"""Score extraction results against hand-checked answer keys."""
import json
import re
from pathlib import Path

# Documents with a planted problem: the correct outcome is NEEDS REVIEW.
EXPECT_REVIEW = {"rc4_summit", "rc7_conflicts", "rc8_messy_totals"}


def get(data, *path):
    """Safely dig into nested dicts/lists; returns None if anything is missing."""
    for step in path:
        try:
            data = data[step]
        except (KeyError, IndexError, TypeError):
            return None
    return data


def norm(value):
    """Normalize so '2150.00', 2150 and '$2,150' compare equal, and '07:00:00' == '07:00'."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip()
    try:
        return round(float(text.replace(",", "").replace("$", "")), 2)
    except ValueError:
        pass
    if re.fullmatch(r"\d{2}:\d{2}:\d{2}", text):
        text = text[:5]
    return text.lower()


def norm_list(values):
    return sorted((norm(v) for v in values), key=str)


def checks(key, got):
    """Yield (field, expected, actual) for every field we score.

    Free-text fields (facility names, notes, descriptions) are not scored here:
    the same meaning can be worded many ways, so exact matching would be unfair.
    """
    yield "broker_load_number", key["broker_load_number"], got.get("broker_load_number")
    yield "broker_mc", key.get("broker_mc"), got.get("broker_mc")
    yield "tracking_required", key.get("tracking_required", False), got.get("tracking_required", False)

    for field in ["total", "linehaul", "fuel_surcharge"]:
        yield f"rate.{field}", get(key, "rate", field), get(got, "rate", field)
    for group in ["accessorials", "reimbursements"]:
        yield (f"rate.{group} (amounts)",
               norm_list(a.get("amount") for a in get(key, "rate", group) or []),
               norm_list(a.get("amount") for a in get(got, "rate", group) or []))
    yield ("penalties (amount|source)",
           sorted(f"{norm(p.get('amount'))}|{p.get('source')}" for p in key.get("penalties", [])),
           sorted(f"{norm(p.get('amount'))}|{p.get('source')}" for p in got.get("penalties") or []))
    yield "has conflicts", bool(key.get("conflicts")), bool(got.get("conflicts"))
    yield "commodity.weight_lbs", get(key, "commodity", "weight_lbs"), get(got, "commodity", "weight_lbs")
    yield "commodity.pallet_count", get(key, "commodity", "pallet_count"), get(got, "commodity", "pallet_count")

    key_stops = sorted(key["stops"], key=lambda s: s["sequence"])
    got_stops = sorted(got.get("stops") or [], key=lambda s: s.get("sequence") or 0)
    yield "stop count", len(key_stops), len(got_stops)
    for i, ks in enumerate(key_stops):
        gs = got_stops[i] if i < len(got_stops) else {}
        p = f"stops[{i + 1}]"
        yield f"{p}.stop_type", ks["stop_type"], gs.get("stop_type")
        for field in ["city", "state", "zip_code"]:
            yield f"{p}.{field}", get(ks, "location", field), get(gs, "location", field)
        for field in ["kind", "date", "start", "end"]:
            yield f"{p}.time.{field}", get(ks, "time", field), get(gs, "time", field)
        yield (f"{p}.reference values",
               norm_list(r["value"] for r in ks.get("references", [])),
               norm_list(r.get("value") for r in gs.get("references") or []))


def main():
    total_right = total_fields = silent = docs = docs_correct = 0
    # Each answer key can score several result files: the .txt version plus any
    # PDF variants (rc1_peachtree_pdf, rc9_multistop_scan) of the same load.
    runs = []
    for key_path in sorted(Path("answer_keys").glob("*.json")):
        for variant in ("", "_pdf", "_scan"):
            result_path = Path("results") / f"{key_path.stem}{variant}.json"
            if result_path.exists():
                runs.append((key_path, result_path))
            elif variant == "":
                print(f"{key_path.stem:24} no result file - run extract.py first")

    for key_path, result_path in runs:
        name = result_path.stem

        key = json.loads(key_path.read_text())
        result = json.loads(result_path.read_text())
        got = result.get("raw") or {}
        flagged = bool(result.get("errors"))

        misses = []
        for field, expected, actual in checks(key, got):
            total_fields += 1
            exp = expected if isinstance(expected, list) else norm(expected)
            act = actual if isinstance(actual, list) else norm(actual)
            if exp == act:
                total_right += 1
            else:
                misses.append(f"{field}: expected {expected!r}, got {actual!r}")

        score = f"{len(list(checks(key, got))) - len(misses)}/{len(list(checks(key, got)))} fields"
        docs += 1
        if not misses:
            docs_correct += 1
        if key_path.stem in EXPECT_REVIEW:
            status = "NEEDS REVIEW (expected)" if flagged else "MISSED - should have been flagged!"
        elif flagged:
            status = "NEEDS REVIEW"
        elif misses:
            status = "OK  <-- SILENT ERRORS"
            silent += 1
        else:
            status = "OK"
        print(f"{name:24} {status:34} {score}")
        for miss in misses:
            print(f"    - {miss}")

    if total_fields:
        print(f"\nField accuracy:    {total_right}/{total_fields} = {total_right / total_fields:.1%}")
        print(f"Document accuracy: {docs_correct}/{docs} = {docs_correct / docs:.1%} (every scored field right)")
        print(f"Documents with silent errors: {silent}")


if __name__ == "__main__":
    main()
