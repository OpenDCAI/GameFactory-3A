"""Run from the repository root: python test/test_3d_object_compose.py.

Build a rigid body + armour + weapon preview. Edit CONFIG below to use your
own assets. This example does not test skinned clothing or cloth simulation.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models.common.glb_utils import glb_json_chunk
from operators.gen_3d_object.operator import Gen3DObjectOperator
from operators.gen_3d_object.funcs.code_asset_templates.human_template import synth

OUTPUT_DIR = Path("test_data/outputs/compose_demo")
USE_DEMO_ASSETS = True
# Positions and lengths are metres; rotations are XYZ degrees in Y-up space.
CONFIG = {
    "body": "test_data/inputs/character.glb",
    "armour": "test_data/inputs/armour.glb",
    "height_metres": 1.75,
    "subject": "character with armour and sword",
    # Whole-shell overlay; the segmented fit still needs piece_stretch support.
    "segment_armour": False,
    "socket_definitions": {
        "weapon_hand": {"slot": "hand", "side": "r", "bone": "RightHand"},
        "back": {"slot": "torso", "offset": [0, 0.04, -0.08], "bone": "Chest"},
    },
    # Optional overrides for measured slot placement.
    "slot_definitions": {},
    "grip_templates": {
        "sword": {"socket": "weapon_hand", "length": 0.9,
                  "rotation": [0, 0, -90], "offset": [0.04, 0, 0.02],
                  "long_axis": "x"},
    },
    "weapons": [{"id": "weapon-sword", "kind": "sword",
                 "source": "test_data/inputs/sword.glb"}],
}
# Imported meshes trigger the provenance gate; preview mode retains its warnings.
STRICT = False


def test_compose() -> None:
    config = {**CONFIG, "weapons": [dict(weapon) for weapon in CONFIG["weapons"]]}
    if USE_DEMO_ASSETS:
        library = synth.write_library(str(OUTPUT_DIR / "inputs"))
        config.update(body=library["bodies"]["nominal"],
                      armour=library["armours"]["short_plate"])
        config["weapons"][0]["source"] = library["weapons"]["sword"]

    operator = Gen3DObjectOperator(model=None, output_dir=str(OUTPUT_DIR))
    result = operator.run({"task_id": "compose_demo", "compose": config, "strict": STRICT})
    assert result.get("glb_path"), result.get("warnings")
    doc = glb_json_chunk(Path(result["glb_path"]).read_bytes())
    names = {node.get("name") for node in doc["nodes"]}
    expected = {"figure", *(w["id"] for w in config["weapons"])}
    if config.get("armour") and not config["segment_armour"]:
        expected.add("armour-whole")
    assert expected <= names, names
    records = json.loads(Path(result["compose"]["sockets_path"]).read_text())
    assert {row["id"] for row in records} == set(config["socket_definitions"])
    for row in records:
        assert row.get("bone") == config["socket_definitions"][row["id"]].get("bone")
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}")
    print(f"Compose passed: {OUTPUT_DIR / 'compose_demo.glb'}")
    print(f"Report: {OUTPUT_DIR / 'compose_demo_compose/report.json'}")


if __name__ == "__main__":
    test_compose()
