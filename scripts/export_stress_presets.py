"""Export the fixed stress catalogue as normal validated scenario JSON files."""

import argparse
import hashlib
import json
from pathlib import Path

from scenarios.stress_presets import CALIBRATION_MAP, CATALOG_VERSION, STRESS_PRESETS


def export_presets(output, *, seed=7):
    # Validate everything before creating a directory; no simulator is needed.
    recipes = [(preset, preset.scenario(seed=seed)) for preset in STRESS_PRESETS]
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    for preset, recipe in recipes:
        filename = preset.id + ".json"
        content = json.dumps(recipe.model_dump(mode="json"), indent=2, allow_nan=False) + "\n"
        with (output / filename).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        entries.append({"id": preset.id, "family": preset.family.value, "intended_stress": preset.intended_stress,
                        "description": preset.description, "file": filename,
                        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()})
    index = {"catalog_version": CATALOG_VERSION, "seed": seed, "map": CALIBRATION_MAP,
             "scope": "Ground-truth baseline stress recipes; consult docs/collision-stress.md for calibration and limits.",
             "presets": entries}
    with (output / "index.json").open("x", encoding="utf-8") as stream:
        json.dump(index, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing files are never overwritten")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    index = export_presets(args.output, seed=args.seed)
    print(json.dumps({"catalog_version": index["catalog_version"], "presets": len(index["presets"]),
                      "output": str(args.output.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
