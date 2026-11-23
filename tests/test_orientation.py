import copy
import json

import jsonschema
import pytest

from virtuoso_workbench import server as app
from virtuoso_workbench.checks import connectivity_checks
from virtuoso_workbench.orientation import ORIENTATIONS, compose, flipped_orientation
from virtuoso_workbench.requests import RequestError, apply_changes, validate_changes

MAPPING = {
    "R0": "MY",
    "R90": "MXR90",
    "R180": "MX",
    "R270": "MYR90",
    "MY": "R0",
    "MYR90": "R270",
    "MX": "R180",
    "MXR90": "R90",
}


def flip(instance="MN1", start="R0", end="MY"):
    return {"op": "flip-horizontal", "instance": instance, "from_orientation": start, "to_orientation": end}


@pytest.mark.parametrize("start,end", MAPPING.items())
def test_all_orientations_world_composition_and_involution(demo, start, end):
    assert flipped_orientation(start) == end
    assert flipped_orientation(end) == start
    assert ORIENTATIONS[end] == compose(ORIENTATIONS["MY"], ORIENTATIONS[start])
    assert compose(ORIENTATIONS["MY"], compose(ORIENTATIONS["MY"], ORIENTATIONS[start])) == ORIENTATIONS[start]
    snapshot = copy.deepcopy(demo)
    selected = next(instance for instance in snapshot["instances"] if instance["name"] == "MN1")
    selected["orientation"] = start
    validated, preview = apply_changes([flip(start=start, end=end), flip(start=end, end=start)], snapshot, preview=True)
    assert len(validated) == 2
    restored = next(instance for instance in preview["instances"] if instance["name"] == "MN1")
    assert restored["orientation"] == start
    assert restored["bbox"] == selected["bbox"]
    for original, actual in zip(selected["terminals"], restored["terminals"]):
        assert actual["xy"] == pytest.approx(original["xy"])


def test_compound_convention_matches_cadence_point_example():
    point = (2.15, 2.05)
    for orientation, expected in [("MYR90", (-2.05, -2.15)), ("MXR90", (2.05, 2.15))]:
        assert (
            tuple(sum(row[index] * point[index] for index in (0, 1)) for row in ORIENTATIONS[orientation]) == expected
        )


def test_flip_geometry_wires_stay_in_place_and_demo_detects_break(demo):
    original = copy.deepcopy(demo)
    validated, preview = apply_changes([flip()], demo, preview=True)
    before = next(instance for instance in demo["instances"] if instance["name"] == "MN1")
    after = next(instance for instance in preview["instances"] if instance["name"] == "MN1")
    center_twice = sum(point[0] for point in before["bbox"])
    assert after["bbox"] == before["bbox"]
    assert after["xy"] == pytest.approx([center_twice - before["xy"][0], before["xy"][1]])
    for left, right in zip(before["terminals"], after["terminals"]):
        assert right["net"] == left["net"]
        assert right["xy"] == pytest.approx([center_twice - left["xy"][0], left["xy"][1]])
    assert preview["wires"] == demo["wires"] and demo == original
    assert any(finding["code"] == "dangling-wire-end" for finding in connectivity_checks(preview))
    schema = json.loads((app.ROOT / "schemas/design-request-v1.json").read_text())
    jsonschema.Draft202012Validator({"$ref": "#/$defs/change", "$defs": schema["$defs"]}).validate(validated[0])
    assert validate_changes([flip()], demo) == validated


def test_flip_preview_mirrors_all_source_pin_anchors_without_revision_change(demo):
    without_metadata = copy.deepcopy(demo)
    snapshot = copy.deepcopy(demo)
    selected = next(instance for instance in snapshot["instances"] if instance["name"] == "MN1")
    selected["terminals"][0]["source_pin_anchors"] = [[-4, 0.7], [-4.2, 0.7]]
    revision = app.revision(snapshot)
    assert revision == app.revision(without_metadata)
    _, preview = apply_changes([flip()], snapshot, preview=True)
    _, plain_preview = apply_changes([flip()], without_metadata, preview=True)
    after = next(instance for instance in preview["instances"] if instance["name"] == "MN1")
    assert after["terminals"][0]["source_pin_anchors"] == [[-4, 0.7], [-3.8, 0.7]]
    assert after["terminals"][0]["xy"] == pytest.approx([-4, 0.7])
    assert app.revision(preview) == app.revision(plain_preview)
    _, restored = apply_changes([flip(), flip(start="MY", end="R0")], snapshot, preview=True)
    restored_instance = next(instance for instance in restored["instances"] if instance["name"] == "MN1")
    assert restored_instance["terminals"][0]["source_pin_anchors"] == selected["terminals"][0]["source_pin_anchors"]


@pytest.mark.parametrize(
    "changes",
    [
        [flip(instance="missing")],
        [flip(start="MY", end="R0")],
        [flip(end="MX")],
        [flip(start="bad")],
        [{**flip(), "to_orientation": None}],
        [{"op": "flip-horizontal", "instance": "MN1"}],
        [flip(), flip()],
        [{"op": "remove-instance", "name": "MN1"}, flip()],
    ],
)
def test_invalid_or_out_of_order_flip(demo, changes):
    before = copy.deepcopy(demo)
    with pytest.raises(RequestError):
        validate_changes(changes, demo)
    assert demo == before


def test_new_instance_has_default_orientation_but_unknown_geometry(demo):
    master = demo["instances"][0]
    changes = [
        {"op": "add-instance", "name": "NEW", "library": master["library"], "cell": master["master"], "parameters": {}},
        flip("NEW"),
    ]
    for preview_mode in (False, True):
        _, preview = apply_changes(changes, demo, preview=preview_mode)
        assert preview["instances"][-1]["orientation"] == "MY"
        assert preview["instances"][-1]["bbox"] == []
    assert connectivity_checks(apply_changes([], demo, preview=True)[1]) == connectivity_checks(demo)
