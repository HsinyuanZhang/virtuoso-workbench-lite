"""Orthogonal Virtuoso transforms in world coordinates."""

from typing import Any

Matrix = tuple[tuple[int, int], tuple[int, int]]


def compose(left: Matrix, right: Matrix) -> Matrix:
    def cell(i: int, j: int) -> int:
        return sum(left[i][k] * right[k][j] for k in (0, 1))

    return ((cell(0, 0), cell(0, 1)), (cell(1, 0), cell(1, 1)))


ORIENTATIONS: dict[str, Matrix] = {
    "R0": ((1, 0), (0, 1)),
    "R90": ((0, -1), (1, 0)),
    "R180": ((-1, 0), (0, -1)),
    "R270": ((0, 1), (-1, 0)),
    "MY": ((-1, 0), (0, 1)),
    "MX": ((1, 0), (0, -1)),
}
ORIENTATIONS["MYR90"] = compose(ORIENTATIONS["R90"], ORIENTATIONS["MY"])
ORIENTATIONS["MXR90"] = compose(ORIENTATIONS["R90"], ORIENTATIONS["MX"])


def flipped_orientation(orientation: str) -> str:
    if orientation not in ORIENTATIONS:
        raise ValueError("Unsupported instance orientation.")
    matrix = compose(ORIENTATIONS["MY"], ORIENTATIONS[orientation])
    return next(name for name, candidate in ORIENTATIONS.items() if candidate == matrix)


def flip_instance(instance: dict[str, Any]) -> None:
    instance["orientation"] = flipped_orientation(instance["orientation"])
    bbox = instance.get("bbox", [])
    if len(bbox) != 2:
        return
    center_twice = bbox[0][0] + bbox[1][0]

    def mirror(point: list[float]) -> list[float]:
        return [center_twice - point[0], point[1]]

    reflected = [mirror(point) for point in bbox]
    instance["bbox"] = [
        [min(point[axis] for point in reflected) for axis in (0, 1)],
        [max(point[axis] for point in reflected) for axis in (0, 1)],
    ]
    if instance.get("xy") is not None:
        instance["xy"] = mirror(instance["xy"])
    for terminal in instance["terminals"]:
        if terminal.get("xy") is not None:
            terminal["xy"] = mirror(terminal["xy"])
