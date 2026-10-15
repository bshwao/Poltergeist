"""Export deterministic JSON Schema: python -m scripts.export_contracts [--check]."""

import argparse
import json
from pathlib import Path

from shared.contracts import PoltergeistContracts

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "models.schema.json"


def render_schema() -> str:
    schema = PoltergeistContracts.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return json.dumps(schema, indent=2, sort_keys=True, allow_nan=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the committed schema is stale")
    args = parser.parse_args()
    expected = render_schema()
    if args.check:
        if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text(encoding="utf-8") != expected:
            parser.exit(1, "Schema is stale. Run python -m scripts.export_contracts\n")
        print("Shared JSON Schema is current.")
    else:
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(expected, encoding="utf-8", newline="\n")
        print(f"Wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
