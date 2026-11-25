"""Versioned, read-only symbol package support."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA = "virtuoso-workbench/library-symbols-v1"
NAME = re.compile(r"[A-Za-z0-9_.$+!@-]{1,128}")
IDENTITY = re.compile(r"sha256:[0-9a-f]{64}")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SYMBOLS = 256
MAX_PRIMITIVES = 10000
MAX_TEXT = 4096
MAX_TEXT_BYTES = 1024 * 1024
MAX_GEOMETRY_ITEMS = 100000
MAX_DIAGNOSTICS = 128
MAX_JSON_DEPTH = 32
MAX_COORDINATE = 1e12
MAX_TERMINAL_NAME_LENGTH = 128
TERMINAL_DIRECTIONS = {"input", "output", "inputOutput", "tristate", "feedthrough", "unused"}
ORIENTATIONS = {"R0", "R90", "R180", "R270", "MY", "MYR90", "MX", "MXR90"}


class SymbolPackageError(ValueError):
    pass


def _is_valid_terminal_name(value: Any) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_TERMINAL_NAME_LENGTH:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(unicodedata.category(character) == "Cc" for character in value)


def _canonical(value: dict[str, Any]) -> bytes:
    content = {key: item for key, item in value.items() if key not in ("digest", "exported_at")}
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def content_digest(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def library_identity(path: str) -> str:
    if not isinstance(path, str) or not path.strip() or len(path) > 4096:
        raise SymbolPackageError("Bridge did not return a library path.")
    normalized = os.path.normpath(path.strip()).encode("utf-8")
    return "sha256:" + hashlib.sha256(normalized).hexdigest()


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SymbolPackageError("Symbol coordinates must be numbers.")
    result = float(value)
    if not math.isfinite(result) or abs(result) > MAX_COORDINATE:
        raise SymbolPackageError("Symbol coordinate is non-finite or outside the supported range.")
    return result


def _point(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise SymbolPackageError("A symbol point must contain two coordinates.")
    return [_number(value[0]), _number(value[1])]


def _bbox(value: Any) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 2:
        raise SymbolPackageError("A symbol bounding box must contain two points.")
    first, second = _point(value[0]), _point(value[1])
    if first[0] > second[0] or first[1] > second[1]:
        raise SymbolPackageError("A symbol bounding box is not ordered.")
    return [first, second]


def _exact_fields(value: dict[str, Any], required: set[str], allowed: set[str], label: str) -> None:
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise SymbolPackageError(f"Invalid {label} fields.")


def _bounded_text(value: Any, label: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise SymbolPackageError(f"Invalid {label}.")
    return value


def _validate_json_depth(value: Any) -> None:
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise SymbolPackageError("Symbol package JSON is nested too deeply.")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def build_package(
    library: str,
    identity: str,
    symbols: list[dict[str, Any]],
    source_version: str | None = None,
    exported_at: str | None = None,
) -> dict[str, Any]:
    package: dict[str, Any] = {
        "schema": SCHEMA,
        "library": {"name": library, "identity": identity},
        "coordinate_system": {"axes": "x-right-y-up", "units": "OA user units"},
        "symbols": symbols,
        "exported_at": exported_at or datetime.now(timezone.utc).isoformat(),
    }
    if source_version is not None:
        package["source_version"] = source_version
    package["digest"] = content_digest(package)
    return validate_package(package)


def validate_package(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SymbolPackageError("Unsupported symbol package schema.")
    _validate_json_depth(value)
    _exact_fields(
        value,
        {"schema", "library", "coordinate_system", "symbols", "exported_at", "digest"},
        {"schema", "library", "coordinate_system", "symbols", "exported_at", "digest", "source_version"},
        "symbol package",
    )
    if value.get("schema") != SCHEMA:
        raise SymbolPackageError("Unsupported symbol package schema.")
    library = value.get("library")
    if not isinstance(library, dict) or set(library) != {"name", "identity"}:
        raise SymbolPackageError("Symbol package needs one library name and identity.")
    if not isinstance(library["name"], str) or not NAME.fullmatch(library["name"]):
        raise SymbolPackageError("Invalid package library name.")
    if not isinstance(library["identity"], str) or not IDENTITY.fullmatch(library["identity"]):
        raise SymbolPackageError("Invalid package library identity.")
    coordinates = value.get("coordinate_system")
    if (
        not isinstance(coordinates, dict)
        or not {"axes", "units"}.issubset(coordinates)
        or not set(coordinates).issubset({"axes", "units", "scale", "grid"})
        or coordinates.get("axes") != "x-right-y-up"
        or coordinates.get("units") != "OA user units"
    ):
        raise SymbolPackageError("Unsupported symbol coordinate system.")
    for optional in ("scale", "grid"):
        if optional in coordinates:
            coordinate_value = _number(coordinates[optional])
            if coordinate_value <= 0:
                raise SymbolPackageError(f"Invalid coordinate {optional}.")
            if optional == "scale" and coordinate_value != 1.0:
                raise SymbolPackageError("Coordinate scale must be 1 when provided.")
    symbols = value.get("symbols")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS:
        raise SymbolPackageError("Symbol package has no symbols or exceeds the symbol limit.")
    keys: set[tuple[str, str]] = set()
    geometry_items = 0
    text_bytes = 0
    for symbol in symbols:
        if not isinstance(symbol, dict):
            raise SymbolPackageError("Invalid symbol record.")
        _exact_fields(
            symbol,
            {"cell", "view", "origin", "bbox", "primitives", "terminals", "complete", "diagnostics"},
            {"cell", "view", "origin", "bbox", "primitives", "terminals", "complete", "diagnostics"},
            "symbol",
        )
        cell, view = symbol.get("cell"), symbol.get("view")
        if not isinstance(cell, str) or not NAME.fullmatch(cell) or view != "symbol":
            raise SymbolPackageError("Invalid symbol cell or view.")
        if (cell, view) in keys:
            raise SymbolPackageError("Duplicate symbol binding.")
        keys.add((cell, view))
        _bbox(symbol.get("bbox"))
        _point(symbol.get("origin"))
        complete, diagnostics = symbol.get("complete"), symbol.get("diagnostics")
        if not isinstance(complete, bool) or not isinstance(diagnostics, list) or len(diagnostics) > MAX_DIAGNOSTICS:
            raise SymbolPackageError("Invalid symbol completeness metadata.")
        for diagnostic in diagnostics:
            text_bytes += len(_bounded_text(diagnostic, "symbol diagnostic").encode("utf-8"))
        primitives = symbol.get("primitives")
        terminals = symbol.get("terminals")
        if (
            not isinstance(primitives, list)
            or not isinstance(terminals, list)
            or len(primitives) + len(terminals) > MAX_PRIMITIVES
        ):
            raise SymbolPackageError("Symbol geometry exceeds the primitive limit.")
        geometry_items += len(primitives) + len(terminals)
        unsupported_geometry = False
        drawable_geometry = False
        for primitive in primitives:
            if not isinstance(primitive, dict):
                raise SymbolPackageError("Invalid artwork primitive.")
            kind = primitive.get("type")
            if kind == "line":
                _exact_fields(
                    primitive, {"type", "points", "layer", "purpose"}, {"type", "points", "layer", "purpose"}, "line"
                )
                points = primitive.get("points")
                if not isinstance(points, list) or not 2 <= len(points) <= MAX_PRIMITIVES:
                    raise SymbolPackageError("A line needs at least two points.")
                for point in points:
                    _point(point)
                geometry_items += len(points)
                drawable_geometry = True
            elif kind == "rect":
                _exact_fields(
                    primitive, {"type", "bbox", "layer", "purpose"}, {"type", "bbox", "layer", "purpose"}, "rectangle"
                )
                _bbox(primitive.get("bbox"))
                geometry_items += 2
                drawable_geometry = True
            elif kind == "polygon":
                _exact_fields(
                    primitive, {"type", "points", "layer", "purpose"}, {"type", "points", "layer", "purpose"}, "polygon"
                )
                points = primitive.get("points")
                if not isinstance(points, list) or not 3 <= len(points) <= MAX_PRIMITIVES:
                    raise SymbolPackageError("A polygon needs at least three points.")
                for point in points:
                    _point(point)
                geometry_items += len(points)
                drawable_geometry = True
            elif kind in ("ellipse", "arc"):
                fields = {"type", "bbox", "layer", "purpose"}
                if kind == "arc":
                    fields.update(("start_angle", "stop_angle"))
                _exact_fields(primitive, fields, fields, kind)
                ellipse_bbox = _bbox(primitive.get("bbox"))
                if ellipse_bbox[0][0] == ellipse_bbox[1][0] or ellipse_bbox[0][1] == ellipse_bbox[1][1]:
                    raise SymbolPackageError(f"A {kind} primitive needs nonzero width and height.")
                if kind == "arc":
                    _number(primitive.get("start_angle"))
                    _number(primitive.get("stop_angle"))
                geometry_items += 2
                drawable_geometry = True
            elif kind == "label":
                _exact_fields(
                    primitive,
                    {"type", "text", "xy", "layer", "purpose"},
                    {"type", "text", "xy", "layer", "purpose"},
                    "label",
                )
                text_bytes += len(_bounded_text(primitive.get("text"), "static label").encode("utf-8"))
                _point(primitive.get("xy"))
                geometry_items += 1
                drawable_geometry = True
            elif kind == "instance-name":
                _exact_fields(
                    primitive,
                    {"type", "xy", "layer", "purpose"},
                    {"type", "xy", "layer", "purpose"},
                    "instance-name label",
                )
                _point(primitive.get("xy"))
                geometry_items += 1
                drawable_geometry = True
            elif kind == "unsupported":
                _exact_fields(
                    primitive,
                    {"type", "source_type"},
                    {"type", "source_type", "layer", "purpose"},
                    "unsupported primitive",
                )
                source_type = _bounded_text(primitive.get("source_type"), "unsupported primitive type", 128)
                if not source_type:
                    raise SymbolPackageError("Unsupported artwork needs its source type.")
                text_bytes += len(source_type.encode("utf-8"))
                unsupported_geometry = True
            else:
                raise SymbolPackageError("Unknown artwork primitive type.")
            for key in ("layer", "purpose"):
                if key in primitive:
                    text_bytes += len(_bounded_text(primitive[key], f"primitive {key}", 128).encode("utf-8"))
        if complete and unsupported_geometry:
            raise SymbolPackageError("A symbol with unsupported geometry cannot be complete.")
        if complete and not drawable_geometry:
            raise SymbolPackageError("A complete symbol needs renderable artwork.")
        terminal_names: set[str] = set()
        for terminal in terminals:
            if not isinstance(terminal, dict) or not _is_valid_terminal_name(terminal.get("name")):
                raise SymbolPackageError("Invalid symbol terminal.")
            _exact_fields(
                terminal, {"name", "direction", "pin_figures"}, {"name", "direction", "pin_figures"}, "terminal"
            )
            if terminal["direction"] not in TERMINAL_DIRECTIONS:
                raise SymbolPackageError("Invalid symbol terminal direction.")
            terminal_name = terminal["name"]
            text_bytes += len(terminal_name.encode("utf-8"))
            if terminal_name in terminal_names:
                raise SymbolPackageError("Duplicate symbol terminal.")
            terminal_names.add(terminal_name)
            figures = terminal.get("pin_figures")
            if not isinstance(figures, list) or len(figures) > MAX_PRIMITIVES:
                raise SymbolPackageError("Invalid pin figure list.")
            geometry_items += len(figures)
            for figure in figures:
                if not isinstance(figure, dict):
                    raise SymbolPackageError("Invalid pin figure.")
                _exact_fields(figure, {"bbox", "anchors"}, {"bbox", "anchors"}, "pin figure")
                _bbox(figure.get("bbox"))
                anchors = figure.get("anchors")
                if not isinstance(anchors, list) or not 1 <= len(anchors) <= MAX_PRIMITIVES:
                    raise SymbolPackageError("Pin figures need anchor points.")
                for anchor in anchors:
                    _point(anchor)
                geometry_items += len(anchors) + 2
        if geometry_items > MAX_GEOMETRY_ITEMS or text_bytes > MAX_TEXT_BYTES:
            raise SymbolPackageError("Symbol package exceeds the total geometry or text budget.")
    digest = value.get("digest")
    if not isinstance(digest, str) or digest != content_digest(value):
        raise SymbolPackageError("Symbol package digest does not match its content.")
    exported = value.get("exported_at")
    if not isinstance(exported, str) or len(exported) > 64:
        raise SymbolPackageError("Invalid export timestamp.")
    try:
        timestamp = datetime.fromisoformat(exported.replace("Z", "+00:00"))
    except ValueError as error:
        raise SymbolPackageError("Invalid export timestamp.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise SymbolPackageError("Export timestamp must include a timezone.")
    if "source_version" in value and (
        not isinstance(value["source_version"], str) or len(value["source_version"]) > 128
    ):
        raise SymbolPackageError("Invalid source version label.")
    return value


def load_package(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        if target.stat().st_size > MAX_FILE_BYTES:
            raise SymbolPackageError("Symbol package exceeds the file size limit.")
        with target.open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise SymbolPackageError("Could not read symbol package file.") from error
    if len(data) > MAX_FILE_BYTES:
        raise SymbolPackageError("Symbol package exceeds the file size limit.")
    try:
        value = json.loads(
            data.decode("utf-8"),
            parse_constant=lambda _: (_ for _ in ()).throw(SymbolPackageError("Non-finite JSON number.")),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise SymbolPackageError("Invalid symbol package JSON.") from error
    return validate_package(value)


def load_packages(paths: list[str | Path]) -> list[dict[str, Any]]:
    packages: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        package = load_package(path)
        key = (package["library"]["name"], package["library"]["identity"])
        previous = packages.get(key)
        if previous and previous["digest"] != package["digest"]:
            raise SymbolPackageError("Conflicting packages bind the same library identity.")
        packages[key] = package
    return list(packages.values())


def summarize_package(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": package["schema"],
        "library": package["library"],
        "digest": package["digest"],
        "symbol_count": len(package["symbols"]),
        "complete_count": sum(symbol["complete"] for symbol in package["symbols"]),
        "symbols": [
            {
                "cell": symbol["cell"],
                "view": symbol["view"],
                "complete": symbol["complete"],
                "diagnostics": symbol["diagnostics"],
            }
            for symbol in package["symbols"]
        ],
    }


def parse_skill_value(raw: str) -> Any:
    """Parse the bounded proper-list subset printed by SKILL's %L format."""
    if not isinstance(raw, str):
        raise SymbolPackageError("Bridge returned a non-text symbol response.")
    if len(raw) > MAX_FILE_BYTES:
        raise SymbolPackageError("Bridge symbol response exceeds the size limit.")
    tokens: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char.isspace():
            index += 1
        elif char in "()":
            tokens.append(char)
            index += 1
        elif char == '"':
            index += 1
            result = []
            while index < len(raw) and raw[index] != '"':
                if raw[index] == "\\":
                    index += 1
                    if index >= len(raw):
                        raise SymbolPackageError("Malformed quoted Bridge value.")
                    escaped = raw[index]
                    result.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
                else:
                    result.append(raw[index])
                index += 1
            if index >= len(raw):
                raise SymbolPackageError("Unterminated Bridge string.")
            index += 1
            tokens.append(json.dumps("".join(result), ensure_ascii=False))
        else:
            end = index
            while end < len(raw) and not raw[end].isspace() and raw[end] not in "()":
                end += 1
            tokens.append(raw[index:end])
            index = end
        if len(tokens) > 200000:
            raise SymbolPackageError("Bridge symbol response has too many values.")
    position = 0

    def parse(depth: int = 0) -> Any:
        nonlocal position
        if depth > 32 or position >= len(tokens):
            raise SymbolPackageError("Malformed Bridge symbol response.")
        token = tokens[position]
        position += 1
        if token == "(":
            values = []
            while position < len(tokens) and tokens[position] != ")":
                values.append(parse(depth + 1))
            if position >= len(tokens):
                raise SymbolPackageError("Malformed Bridge list.")
            position += 1
            return values
        if token == ")":
            raise SymbolPackageError("Malformed Bridge list.")
        if token.startswith('"'):
            return json.loads(token)
        if token in ("nil", "NIL"):
            return None
        if token in ("t", "T"):
            return True
        try:
            value = float(token)
        except ValueError:
            return token
        if not math.isfinite(value):
            raise SymbolPackageError("Bridge returned a non-finite number.")
        return value

    value = parse()
    if position != len(tokens):
        raise SymbolPackageError("Bridge returned extra symbol data.")
    return value


def decode_bridge_skill_output(raw: str, fallback_decoder: Callable[[str], str]) -> str:
    if not isinstance(raw, str):
        raise SymbolPackageError("Bridge returned a non-text symbol response.")
    if len(raw) > MAX_FILE_BYTES:
        raise SymbolPackageError("Bridge symbol response exceeds the size limit.")
    if raw.lstrip().startswith('"'):
        decoded = parse_skill_value(raw)
        if not isinstance(decoded, str):
            raise SymbolPackageError("Bridge returned an invalid string wrapper.")
    else:
        decoded = fallback_decoder(raw)
    if not isinstance(decoded, str):
        raise SymbolPackageError("Bridge returned a non-text symbol response.")
    if len(decoded) > MAX_FILE_BYTES:
        raise SymbolPackageError("Bridge symbol response exceeds the size limit.")
    return decoded


def _skill_string(value: str) -> str:
    if not NAME.fullmatch(value):
        raise SymbolPackageError("Invalid library or cell name.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _identity_query(library: str) -> str:
    return f"""let((lib)
      lib=ddGetObj({_skill_string(library)})
      if(lib then sprintf(nil "%L" list("OK" lib~>readPath)) else sprintf(nil "%L" list("MISSING"))))"""


def _symbol_query(library: str, cell: str, view: str) -> str:
    lib, master = _skill_string(library), _skill_string(cell)
    return f"""let((cv shapes terminals figures result kind data)
      cv=dbOpenCellViewByType({lib} {master} {_skill_string(view)} nil "r")
      if(cv then
        unwindProtect(
          progn(
            shapes=nil
            foreach(shape cv~>shapes
              kind=shape~>objType
              data=list("unsupported" kind shape~>layerName shape~>purpose shape~>bBox)
              when(kind == "line" data=list("line" shape~>points shape~>layerName shape~>purpose))
              when(kind == "rect" data=list("rect" shape~>bBox shape~>layerName shape~>purpose))
              when(kind == "polygon" data=list("polygon" shape~>points shape~>layerName shape~>purpose))
              when(kind == "ellipse" data=list("ellipse" shape~>bBox shape~>layerName shape~>purpose))
              when(kind == "arc"
                data=list("arc" shape~>ellipseBBox shape~>startAngle shape~>stopAngle shape~>layerName shape~>purpose))
              when(kind == "label"
                data=list("label" shape~>theLabel shape~>xy shape~>layerName shape~>purpose shape~>labelType))
              shapes=cons(data shapes))
            terminals=nil
            foreach(term cv~>terminals
              figures=nil
              foreach(pin term~>pins
                foreach(fig pin~>figs figures=cons(fig~>bBox figures)))
              terminals=cons(list(term~>name term~>direction reverse(figures)) terminals))
            result=list("OK" cv~>bBox list(0 0) reverse(shapes) reverse(terminals) length(cv~>instances))
            sprintf(nil "%L" result))
          dbClose(cv))
        else sprintf(nil "%L" list("MISSING"))))"""


def _point_from_box(value: Any) -> list[float]:
    box = _bbox(value)
    return [(box[0][0] + box[1][0]) / 2, (box[0][1] + box[1][1]) / 2]


def _symbol_from_skill(cell: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list) or len(raw) != 6 or raw[0] != "OK":
        if isinstance(raw, list) and raw and raw[0] == "MISSING":
            raise SymbolPackageError(f"Symbol view is missing for cell {cell}.")
        raise SymbolPackageError("Bridge returned invalid symbol metadata.")
    bbox, origin, raw_shapes, raw_terminals, nested_count = raw[1:]
    if raw_shapes is None:
        raw_shapes = []
    if raw_terminals is None:
        raw_terminals = []
    if (
        not isinstance(raw_shapes, list)
        or not isinstance(raw_terminals, list)
        or len(raw_shapes) > MAX_PRIMITIVES
        or len(raw_terminals) > MAX_PRIMITIVES
    ):
        raise SymbolPackageError("Bridge returned invalid symbol geometry lists.")
    primitives: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    incomplete_geometry = False
    for shape in raw_shapes:
        if not isinstance(shape, list) or not shape:
            raise SymbolPackageError("Bridge returned an invalid artwork record.")
        kind = shape[0]
        if kind == "line" and len(shape) == 4:
            points = shape[1]
            if not isinstance(points, list) or len(points) < 2:
                raise SymbolPackageError("Bridge returned invalid line points.")
            primitives.append({"type": "line", "points": points, "layer": shape[2], "purpose": shape[3]})
        elif kind == "rect" and len(shape) == 4:
            primitives.append({"type": "rect", "bbox": shape[1], "layer": shape[2], "purpose": shape[3]})
        elif kind == "polygon" and len(shape) == 4:
            points = shape[1]
            if not isinstance(points, list) or len(points) < 3:
                raise SymbolPackageError("Bridge returned invalid polygon points.")
            primitives.append({"type": "polygon", "points": points, "layer": shape[2], "purpose": shape[3]})
        elif kind == "ellipse" and len(shape) == 4:
            primitives.append({"type": "ellipse", "bbox": shape[1], "layer": shape[2], "purpose": shape[3]})
        elif kind == "arc" and len(shape) == 6:
            primitives.append(
                {
                    "type": "arc",
                    "bbox": shape[1],
                    "start_angle": shape[2],
                    "stop_angle": shape[3],
                    "layer": shape[4],
                    "purpose": shape[5],
                }
            )
        elif kind == "label" and len(shape) == 6:
            text, xy, layer, purpose, label_type = shape[1:]
            if not isinstance(text, str):
                diagnostics.append("unsupported-dynamic-label")
                continue
            instance_name = (
                bool(re.fullmatch(r"\[\s*@instanceName\s*\]", text.strip()))
                if label_type == "NLPLabel"
                else text.strip() == "cdsName()"
            )
            if instance_name:
                primitives.append({"type": "instance-name", "xy": xy, "layer": layer, "purpose": purpose})
            elif label_type in ("NLPLabel", "ILLabel") or re.search(r"\b(?:cds[A-Za-z0-9_]*|[ip]Par)\s*\(", text):
                diagnostics.append("unsupported-dynamic-label")
            else:
                primitives.append({"type": "label", "text": text, "xy": xy, "layer": layer, "purpose": purpose})
        else:
            if kind != "unsupported" or len(shape) != 5:
                raise SymbolPackageError("Bridge returned an invalid artwork record.")
            source_type = shape[1]
            if not isinstance(source_type, str) or not source_type or len(source_type) > 128:
                raise SymbolPackageError("Bridge returned an invalid unsupported shape type.")
            primitives.append(
                {"type": "unsupported", "source_type": source_type, "layer": shape[2], "purpose": shape[3]}
            )
            diagnostics.append(f"unsupported-shape:{source_type}")
            incomplete_geometry = True
    if not primitives:
        diagnostics.append("empty-symbol-artwork")
        incomplete_geometry = True
    terminals: list[dict[str, Any]] = []
    for terminal in raw_terminals:
        if not isinstance(terminal, list) or len(terminal) != 3 or not isinstance(terminal[0], str):
            raise SymbolPackageError("Bridge returned invalid terminal metadata.")
        if terminal[1] not in TERMINAL_DIRECTIONS:
            raise SymbolPackageError("Bridge returned an invalid terminal direction.")
        raw_figures = [] if terminal[2] is None else terminal[2]
        if not isinstance(raw_figures, list):
            raise SymbolPackageError("Bridge returned invalid terminal pin figures.")
        figures = []
        for figure_box in raw_figures:
            box = _bbox(figure_box)
            figures.append({"bbox": box, "anchors": [_point_from_box(box)]})
        terminals.append({"name": terminal[0], "direction": terminal[1], "pin_figures": figures})
    if (
        isinstance(nested_count, bool)
        or not isinstance(nested_count, (int, float))
        or not math.isfinite(float(nested_count))
        or nested_count < 0
        or int(nested_count) != nested_count
    ):
        raise SymbolPackageError("Bridge returned invalid nested-instance count.")
    if nested_count:
        diagnostics.append("nested-symbol-instances")
        primitives.append({"type": "unsupported", "source_type": "nested-instance"})
        incomplete_geometry = True
    return {
        "cell": cell,
        "view": "symbol",
        "origin": _point(origin),
        "bbox": _bbox(bbox),
        "primitives": primitives,
        "terminals": terminals,
        "complete": not incomplete_geometry,
        "diagnostics": sorted(set(diagnostics)),
    }


def export_package(
    library: str,
    cells: list[str],
    bridge_host: str,
    bridge_port: int,
    view: str = "symbol",
    source_version: str | None = None,
    client_factory: Callable[..., Any] | None = None,
    decoder: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    if (
        not NAME.fullmatch(library)
        or not cells
        or len(cells) > MAX_SYMBOLS
        or any(not NAME.fullmatch(cell) for cell in cells)
    ):
        raise SymbolPackageError("Export needs a valid library and one or more valid cells.")
    if view != "symbol":
        raise SymbolPackageError("Only the symbol view can be exported.")
    if len(set(cells)) != len(cells):
        raise SymbolPackageError("Duplicate cells are not allowed.")
    if not bridge_host or not 1 <= bridge_port <= 65535:
        raise SymbolPackageError("A Bridge host and valid port are required.")
    if client_factory is None or decoder is None:
        try:
            from virtuoso_bridge import VirtuosoClient, decode_skill_output
        except ImportError as error:
            raise SymbolPackageError("Install the supported Bridge package to export symbols.") from error
        if client_factory is None:
            client_factory = VirtuosoClient.local
        if decoder is None:
            decoder = decode_skill_output
    if client_factory is None or decoder is None:
        raise SymbolPackageError("A Bridge client and output decoder are required.")
    with client_factory(host=bridge_host, port=bridge_port, timeout=30) as client:
        identity_result = client.execute_skill(_identity_query(library), timeout=30)
        if not identity_result.ok:
            raise SymbolPackageError("Bridge could not read the library identity.")
        identity_record = parse_skill_value(decode_bridge_skill_output(identity_result.output, decoder))
        if not isinstance(identity_record, list) or len(identity_record) != 2 or identity_record[0] != "OK":
            raise SymbolPackageError("Bridge could not resolve the library.")
        identity_path = identity_record[1]
        identity = library_identity(identity_path)
        symbols = []
        for cell in cells:
            response = client.execute_skill(_symbol_query(library, cell, view), timeout=60)
            if not response.ok:
                raise SymbolPackageError(f"Bridge could not read symbol {cell}.")
            symbols.append(
                _symbol_from_skill(cell, parse_skill_value(decode_bridge_skill_output(response.output, decoder)))
            )
    return build_package(library, identity, symbols, source_version)


def write_package(path: str | Path, package: dict[str, Any], force: bool = False) -> None:
    validated = validate_package(package)
    target = Path(path)
    target = target.parent.resolve() / target.name
    if not target.parent.is_dir():
        raise SymbolPackageError("Export directory does not exist.")
    if os.path.lexists(target) and not force:
        raise SymbolPackageError("Export output exists; pass --force to replace it.")
    payload = json.dumps(validated, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    if len(payload) > MAX_FILE_BYTES:
        raise SymbolPackageError("Symbol package exceeds the file size limit.")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(target) and not force:
            raise SymbolPackageError("Export output exists; pass --force to replace it.")
        if force:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)
            os.unlink(temporary)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
