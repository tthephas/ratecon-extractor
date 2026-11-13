"""Extract structured load data from rate confirmations using Claude."""
import json
import sys
from datetime import date
from pathlib import Path

import anthropic
from anthropic import transform_schema
from dotenv import load_dotenv
from pydantic import ValidationError

from schema import RateConfirmation

load_dotenv()  # reads ANTHROPIC_API_KEY from .env
client = anthropic.Anthropic()

MODEL = "claude-sonnet-5-5"


def simplify(node):
    """Shrink the schema sent to Claude so its compiled grammar stays small.

    1. Every property becomes required: Claude sends null or [] instead of skipping
       a field. Each skippable field doubles the grammar's size.
    2. Money fields become plain numbers instead of "number or string".
    Our Pydantic model still does the full validation afterwards.
    """
    if isinstance(node, list):
        return [simplify(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: simplify(value) for key, value in node.items()}
    if "anyOf" in node:
        options = [opt for opt in node["anyOf"] if opt != {"type": "string"}]
        has_number = {"type": "number"} in node["anyOf"]
        node["anyOf"] = options if has_number else node["anyOf"]
        if len(node["anyOf"]) == 1:
            node.update(node.pop("anyOf")[0])
    if node.get("type") == "object" and "properties" in node:
        node["required"] = list(node["properties"].keys())
    return node


SCHEMA = simplify(transform_schema(RateConfirmation))

SYSTEM_PROMPT = """You extract data from freight rate confirmations sent by a broker to a carrier.

Rules:
- Transcribe exactly what the document says. Do NOT correct math or fill in values
  that are not on the document. If a field is not present, leave it null or empty.
- "SO" (stop-off) and "DEL" mean a delivery stop. "PU" means a pickup stop.
- Number stops in the order the truck will visit them, starting at 1.
- Use 24-hour times (HH:MM) and YYYY-MM-DD dates. If a date has no year, use the
  next occurrence of that date on or after the date the document was received
  (RECEIVED_DATE).
- If the document gives only a flat or all-in rate with no breakdown, put it in
  total and leave linehaul and fuel_surcharge null. Only fill linehaul or
  fuel_surcharge when the document lists them separately.
- Lumper fees paid later with a receipt are reimbursements, not part of the total.
- Charges included in the total (like stop-off pay) are accessorials.
- Penalties are WARNINGS: fines that apply only if the carrier misses an obligation.
  Tag each one load_specific or terms_and_conditions.
- A deduction already applied to the pay (a line item that reduces the total) is an
  accessorial with a NEGATIVE amount, not a penalty.
- If any parts of the document contradict each other (notes vs stop times, two notes
  about the same charge), do NOT pick a side: transcribe the main stop/rate sections
  as written and describe each contradiction in conflicts.
- Do not report rate math mismatches as conflicts; the system checks math separately.
- Set tracking_required true if the document requires tracking OR mentions any fine,
  deduction, or charge for tracking non-compliance (that implies tracking is required).
- Always check reefer temperature against the commodity. Frozen products need a frozen
  set point (about 10F or below). If the commodity is frozen but the temperature is
  above that, or the commodity is fresh/chilled but the set point is frozen, report it
  in conflicts.
- If an appointment is required but not yet set, use kind "appointment_to_schedule".
- Ignore numbers that are not pay, such as total miles.

Return ONLY a JSON object that matches the JSON schema below, with no other text.
Include every field; use null or [] when the document doesn't have it.
"""
SYSTEM_PROMPT += json.dumps(SCHEMA)


def extract(document_text: str, received: date) -> dict:
    """Send one rate con to Claude. Returns the raw extraction plus any validation errors.

    received: the date the document arrived (an email's sent date in production).
    Used to resolve dates with no year, so results don't change based on when we run.
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT.replace("RECEIVED_DATE", received.isoformat()),
        messages=[{"role": "user", "content": document_text}],
    )

    if response.stop_reason != "end_turn":
        return {"raw": None, "errors": [f"unexpected stop_reason: {response.stop_reason}"]}

    text = next(block.text for block in response.content if block.type == "text")
    # Claude sometimes wraps JSON in ```json fences; strip them before parsing.
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": None, "raw_text": text, "errors": ["response was not valid JSON"]}

    # Policy check: our business rules (math, stop order, appointment times).
    try:
        RateConfirmation.model_validate(raw)
        errors = []
    except ValidationError as e:
        errors = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]

    # Policy: a person must resolve any contradiction Claude found.
    errors += [f"conflict: {c}" for c in raw.get("conflicts") or []]

    return {"raw": raw, "errors": errors}


def main():
    samples = sorted(Path("samples").glob("*.txt"))
    if len(sys.argv) > 1:  # optional: run just one file, e.g. python3 extract.py rc3
        samples = [p for p in samples if sys.argv[1] in p.name]

    dates_file = Path("samples/received_dates.json")
    received_dates = json.loads(dates_file.read_text()) if dates_file.exists() else {}

    out_dir = Path("results")
    out_dir.mkdir(exist_ok=True)

    for path in samples:
        received = date.fromisoformat(received_dates.get(path.stem, date.today().isoformat()))
        result = extract(path.read_text(), received)
        (out_dir / f"{path.stem}.json").write_text(json.dumps(result, indent=2))
        status = "OK" if not result["errors"] else "NEEDS REVIEW"
        print(f"{path.stem:18} {status}")
        for err in result["errors"]:
            print(f"    - {err}")


if __name__ == "__main__":
    main()
