import argparse
import copy
import json
import sys
import types
from pathlib import Path

import jsonschema
import pytest

from virtuoso_workbench import cli, server
from virtuoso_workbench import symbols as symbol_module
from virtuoso_workbench.requests import RequestError
from virtuoso_workbench.symbols import (
    SymbolPackageError,
    _symbol_from_skill,
    build_package,
    content_digest,
    decode_bridge_skill_output,
    export_package,
    load_package,
    load_packages,
    parse_skill_value,
    validate_package,
    write_package,
)

IDENTITY = "sha256:" + "a" * 64


def _skill_literal(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "nil"
    if value is True:
        return "t"
    if value is False:
        return "nil"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "(" + " ".join(_skill_literal(item) for item in value) + ")"
    raise TypeError("Unsupported synthetic SKILL value.")


def _bridge_transport_output(value):
    return json.dumps(_skill_literal(value), ensure_ascii=False)


def _legacy_bridge_decoder(raw):
    text = (raw or "").strip().strip('"')
    return text.replace("\\n", "\n").replace('\\"', '"')


def symbol(cell="nmos", complete=True, diagnostics=None):
    return {
        "cell": cell,
        "view": "symbol",
        "origin": [0, 0],
        "bbox": [[-1, -1], [1, 1]],
        "primitives": [{"type": "line", "points": [[0, 0], [1, 0]], "layer": "drawing", "purpose": "drawing"}],
        "terminals": [
            {
                "name": "A",
                "direction": "inputOutput",
                "pin_figures": [
                    {"bbox": [[-1, -1], [-1, 1]], "anchors": [[-1, 0]]},
                    {"bbox": [[-0.75, -1], [-0.75, 1]], "anchors": [[-0.75, 0]]},
                ],
            }
        ],
        "complete": complete,
        "diagnostics": diagnostics or [],
    }


def test_digest_timestamp_tamper_and_binding(tmp_path):
    package = build_package("generic", IDENTITY, [symbol()], exported_at="2026-01-01T00:00:00+00:00")
    later = {**package, "exported_at": "2027-01-01T00:00:00+00:00"}
    assert content_digest(package) == content_digest(later)
    damaged = json.loads(json.dumps(package))
    damaged["symbols"][0]["bbox"][1][0] = 2
    damaged_path = tmp_path / "damaged.json"
    damaged_path.write_text(json.dumps(damaged), encoding="utf-8")
    with pytest.raises(SymbolPackageError, match="digest"):
        load_package(damaged_path)
    assert (
        load_packages(
            [
                _write_package(package, tmp_path / "one.json"),
                _write_package(build_package("generic", "sha256:" + "b" * 64, [symbol()]), tmp_path / "two.json"),
            ]
        )[1]["library"]["identity"]
        != package["library"]["identity"]
    )
    conflict = build_package("generic", IDENTITY, [symbol()], source_version="different-source")
    with pytest.raises(SymbolPackageError, match="Conflicting packages"):
        load_packages(
            [
                _write_package(package, tmp_path / "conflict-base.json"),
                _write_package(conflict, tmp_path / "conflict.json"),
            ]
        )


def test_startup_rejects_conflicting_and_damaged_package_files(tmp_path):
    first = _write_package(build_package("generic", IDENTITY, [symbol()]), tmp_path / "first.json")
    second = _write_package(
        build_package("generic", IDENTITY, [symbol()], source_version="changed"), tmp_path / "second.json"
    )
    arguments = argparse.Namespace(
        bridge_host=None,
        bridge_docker=None,
        bridge_port=65432,
        state_dir=tmp_path / "state",
        symbol_package=[first, second],
    )
    with pytest.raises(SymbolPackageError, match="Conflicting packages"):
        cli.context(arguments)
    damaged = tmp_path / "damaged.json"
    damaged.write_text("{}", encoding="utf-8")
    arguments.symbol_package = [damaged]
    with pytest.raises(SymbolPackageError):
        cli.context(arguments)


def _write_package(package, path):
    write_package(path, package)
    return path


def test_rejects_nonfinite_and_limits(tmp_path, monkeypatch):
    path = tmp_path / "invalid.json"
    for constant in ("NaN", "Infinity", "-Infinity"):
        path.write_text('{"value":' + constant + "}", encoding="utf-8")
        with pytest.raises(SymbolPackageError, match="Non-finite"):
            load_package(path)
    with pytest.raises(SymbolPackageError, match="read symbol package"):
        load_package("missing-symbol-package.json")
    package = build_package("generic", IDENTITY, [symbol()])
    package["symbols"][0]["primitives"][0]["points"][0][0] = 1e99
    package["digest"] = content_digest(package)
    with pytest.raises(SymbolPackageError, match="range"):
        validate_package(package)
    path.write_text('{"deep":' + "[" * 40 + "0" + "]" * 40 + "}", encoding="utf-8")
    with pytest.raises(SymbolPackageError, match="nested too deeply"):
        load_package(path)
    package = build_package("generic", IDENTITY, [symbol()])
    package["exported_at"] = "2026-01-01T00:00:00"
    path.write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(SymbolPackageError, match="timezone"):
        load_package(path)
    package = build_package("generic", IDENTITY, [symbol()])
    package["symbols"] = [symbol(f"cell{index}") for index in range(symbol_module.MAX_SYMBOLS + 1)]
    package["digest"] = content_digest(package)
    path.write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(SymbolPackageError, match="symbol limit"):
        load_package(path)
    monkeypatch.setattr(symbol_module, "MAX_FILE_BYTES", 4)
    path.write_text("12345", encoding="utf-8")
    with pytest.raises(SymbolPackageError, match="size limit"):
        load_package(path)


def test_skill_strings_keep_delimiters_quotes_and_newlines():
    assert parse_skill_value('("R | \\"x\\"\\nnext")') == ['R | "x"\nnext']


@pytest.mark.parametrize("token", ["nan", "NaN", "inf", "Infinity", "-inf", "-Infinity"])
def test_skill_parser_rejects_nonfinite_number_tokens(token):
    with pytest.raises(SymbolPackageError, match="non-finite"):
        parse_skill_value(token)


def test_unverified_shapes_are_explicitly_incomplete():
    raw = ["OK", [[-1, -1], [1, 1]], [0, 0], [["unsupported", "arc", "drawing", "drawing", None]], [], 0]
    exported = _symbol_from_skill("nmos", raw)
    assert exported["complete"] is False
    assert exported["diagnostics"] == ["unsupported-shape:arc"]


def test_export_arc_preserves_ellipse_box_angles_and_package_schema():
    bbox = [[-2, -1], [4, 3]]
    raw = ["OK", bbox, [0, 0], [["arc", bbox, 5.4, 0.7, "device", "drawing"]], [], 0]
    exported = _symbol_from_skill("capacitor", raw)
    package = build_package("generic", IDENTITY, [exported])
    schema = json.loads(
        (Path(symbol_module.__file__).parent / "schemas" / "library-symbols-v1.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(package)
    assert exported["complete"] is True
    assert exported["primitives"] == [
        {"type": "arc", "bbox": bbox, "start_angle": 5.4, "stop_angle": 0.7, "layer": "device", "purpose": "drawing"}
    ]
    assert "shape~>ellipseBBox shape~>startAngle shape~>stopAngle" in symbol_module._symbol_query(
        "generic", "capacitor", "symbol"
    )


def test_dynamic_label_warning_keeps_supported_body_and_static_parentheses():
    raw = [
        "OK",
        [[-1, -1], [1, 1]],
        [0, 0],
        [
            ["line", [[-1, 0], [1, 0]], "drawing", "drawing"],
            ["label", "W=(typ) ", [0, 1], "drawing", "drawing", "normalLabel"],
            ["label", 'W=cdsParam(\\"w\\")', [0, -1], "drawing", "drawing", "ILLabel"],
        ],
        [["A", "inputOutput", None]],
        0,
    ]
    exported = _symbol_from_skill("nmos", raw)
    assert exported["complete"] is True
    assert exported["diagnostics"] == ["unsupported-dynamic-label"]
    assert any(primitive.get("text") == "W=(typ) " for primitive in exported["primitives"])


def test_export_classifies_nlp_labels_and_keeps_name_templates():
    raw = [
        "OK",
        [[-1, -1], [1, 1]],
        [0, 0],
        [
            ["label", "[@cellName]", [-1, 0], "device", "annotate", "NLPLabel"],
            ["label", "W=[@w]", [0, -1], "device", "annotate", "NLPLabel"],
            ["label", "[@instanceName]", [0, 1], "device", "annotate", "NLPLabel"],
            ["label", "cdsName()", [1, 0], "annotate", "drawing", "ILLabel"],
        ],
        [["A", "inputOutput", None]],
        0,
    ]
    exported = _symbol_from_skill("nmos", raw)
    assert exported["complete"] is True
    assert exported["diagnostics"] == ["unsupported-dynamic-label"]
    assert [(primitive["type"], primitive["xy"]) for primitive in exported["primitives"]] == [
        ("instance-name", [0, 1]),
        ("instance-name", [1, 0]),
    ]


def test_nil_symbol_lists_are_incomplete_not_type_errors():
    exported = _symbol_from_skill("empty", ["OK", [[-1, -1], [1, 1]], [0, 0], None, None, 0])
    assert exported["complete"] is False
    assert exported["diagnostics"] == ["empty-symbol-artwork"]


def test_runtime_validation_is_strict_and_rejects_complete_unsupported_geometry():
    package = build_package("generic", IDENTITY, [symbol()])
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/virtuoso_workbench/schemas/library-symbols-v1.json").read_text()
    )
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(package)
    unit_scale = copy.deepcopy(package)
    unit_scale["coordinate_system"]["scale"] = 1.0
    unit_scale["digest"] = content_digest(unit_scale)
    validate_package(unit_scale)
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(unit_scale)
    nonunit_scale = copy.deepcopy(package)
    nonunit_scale["coordinate_system"]["scale"] = 2.0
    nonunit_scale["digest"] = content_digest(nonunit_scale)
    with pytest.raises(SymbolPackageError, match="scale must be 1"):
        validate_package(nonunit_scale)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(nonunit_scale)
    invalid = copy.deepcopy(package)
    invalid["symbols"][0]["terminals"][0]["direction"] = "sideways"
    invalid["digest"] = content_digest(invalid)
    with pytest.raises(SymbolPackageError, match="direction"):
        validate_package(invalid)
    invalid = copy.deepcopy(package)
    invalid["symbols"][0]["primitives"][0]["extra"] = True
    invalid["digest"] = content_digest(invalid)
    with pytest.raises(SymbolPackageError, match="fields"):
        validate_package(invalid)
    invalid = copy.deepcopy(package)
    invalid["symbols"][0]["primitives"].append({"type": "unsupported", "source_type": "arc"})
    invalid["digest"] = content_digest(invalid)
    with pytest.raises(SymbolPackageError, match="cannot be complete"):
        validate_package(invalid)
    invalid = build_package("generic", IDENTITY, [symbol(complete=False)])
    invalid["symbols"][0]["primitives"] = [{"type": "unsupported", "source_type": "x" * 129}]
    invalid["digest"] = content_digest(invalid)
    with pytest.raises(SymbolPackageError, match="unsupported primitive type"):
        validate_package(invalid)
    invalid["symbols"][0]["primitives"] = [{"type": "unsupported", "source_type": "arc"}]
    invalid["symbols"][0]["diagnostics"] = ["x" * 4097]
    invalid["digest"] = content_digest(invalid)
    with pytest.raises(SymbolPackageError, match="symbol diagnostic"):
        validate_package(invalid)


def test_total_geometry_budget_is_enforced():
    package = build_package("generic", IDENTITY, [symbol()])
    points = [[0, 0]] * 10000
    package["symbols"][0]["primitives"] = [
        {"type": "line", "points": list(points), "layer": "drawing", "purpose": "drawing"} for _ in range(11)
    ]
    package["digest"] = content_digest(package)
    with pytest.raises(SymbolPackageError, match="total geometry"):
        validate_package(package)


class Result:
    def __init__(self, output, ok=True):
        self.output = output
        self.ok = ok


class FakeBridge:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute_skill(self, query, **_):
        self.queries.append(query)
        if "ddGetObj" in query:
            return Result('("OK" "/synthetic/library")')
        if "missingCell" in query:
            return Result('("MISSING")')
        return Result(
            '("OK" ((-1 -1) (1 1)) (0 0) (("line" ((0 0) (1 0)) "drawing" "drawing") ("polygon" ((0 0) (1 0) (0 1)) "drawing" "drawing") ("ellipse" ((-0.5 -0.5) (0.5 0.5)) "drawing" "drawing") ("label" "R | \\"x\\"\\nnext" (0 0) "drawing" "drawing" "normalLabel")) (("A" "inputOutput" (((-1 -1) (-1 1)) ((-0.75 -1) (-0.75 1))))) 0)'
        )


def test_export_is_read_only_and_missing_cell_leaves_no_output(tmp_path):
    bridge = FakeBridge()
    package = export_package(
        "generic", ["nmos"], "local", 65432, client_factory=lambda **_: bridge, decoder=lambda value: value
    )
    assert package["symbols"][0]["terminals"][0]["pin_figures"]
    assert {primitive["type"] for primitive in package["symbols"][0]["primitives"]} == {
        "line",
        "polygon",
        "ellipse",
        "label",
    }
    assert all('"r"' in query or "ddGetObj" in query for query in bridge.queries)
    assert all(not any(token in query for token in ("dbSave", "dbCreate", "cdfEvaluate")) for query in bridge.queries)
    target = tmp_path / "failed.json"
    with pytest.raises(SymbolPackageError, match="missing"):
        export_package(
            "generic",
            ["missingCell"],
            "local",
            65432,
            client_factory=lambda **_: FakeBridge(),
            decoder=lambda value: value,
        )
    assert not target.exists()
    write_package(tmp_path / "ok.json", package)
    with pytest.raises(SymbolPackageError, match="exists"):
        write_package(tmp_path / "ok.json", package)
    assert load_package(tmp_path / "ok.json")["digest"] == package["digest"]


def test_bridge_transport_wrapper_preserves_nested_skill_escaping():
    values = [
        "OK",
        'cdsParam("w")',
        'quote: "x"',
        r"path: \\eda\\cell",
        "line1\nline2",
        "café Ω",
    ]
    skill_output = _skill_literal(values)
    raw_output = json.dumps(skill_output, ensure_ascii=False)

    assert decode_bridge_skill_output(raw_output, _legacy_bridge_decoder) == skill_output
    assert parse_skill_value(skill_output) == values
    with pytest.raises(SymbolPackageError, match="extra symbol data"):
        parse_skill_value(_legacy_bridge_decoder(raw_output))


def test_bridge_transport_wrapper_rejects_truncated_and_oversized_values():
    truncated_wrapper = _bridge_transport_output(["OK"])[:-1]
    with pytest.raises(SymbolPackageError, match="Unterminated Bridge string"):
        decode_bridge_skill_output(truncated_wrapper, _legacy_bridge_decoder)

    malformed_inner = json.dumps('( "OK"', ensure_ascii=False)
    with pytest.raises(SymbolPackageError, match="Malformed Bridge list"):
        parse_skill_value(decode_bridge_skill_output(malformed_inner, _legacy_bridge_decoder))

    oversized = "x" * (symbol_module.MAX_FILE_BYTES + 1)
    with pytest.raises(SymbolPackageError, match="size limit"):
        decode_bridge_skill_output(oversized, _legacy_bridge_decoder)


def test_export_decodes_wrapped_bridge_output_and_preserves_static_labels():
    symbol_value = [
        "OK",
        [[-1, -1], [1, 1]],
        [0, 0],
        [
            ["line", [[0, 0], [1, 0]], "drawing", "drawing"],
            ["label", 'cdsParam("w")', [0, 0], "drawing", "drawing", "ILLabel"],
            ["label", 'width (typ) "quoted" \\path Ω', [0, 0], "drawing", "drawing", "normalLabel"],
        ],
        [["A", "inputOutput", [[[-1, -1], [-1, 1]]]]],
        0,
    ]

    class WrappedBridge:
        def __init__(self):
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute_skill(self, query, **_):
            self.queries.append(query)
            value = ["OK", "/synthetic/library"] if "ddGetObj" in query else symbol_value
            return Result(_bridge_transport_output(value))

    package = export_package(
        "generic",
        ["nmos"],
        "local",
        65432,
        client_factory=lambda **_: WrappedBridge(),
        decoder=_legacy_bridge_decoder,
    )
    exported = package["symbols"][0]
    labels = [primitive["text"] for primitive in exported["primitives"] if primitive["type"] == "label"]
    assert exported["complete"] is True
    assert "unsupported-dynamic-label" in exported["diagnostics"]
    assert labels == ['width (typ) "quoted" \\path Ω']


def test_export_and_inspect_accept_bus_terminal_names(tmp_path, capsys):
    class BusTerminalBridge(FakeBridge):
        def execute_skill(self, query, **kwargs):
            result = super().execute_skill(query, **kwargs)
            return Result(result.output.replace('"A"', '"IN<3:0>"'), result.ok)

    package = export_package(
        "generic",
        ["nmos"],
        "local",
        65432,
        client_factory=lambda **_: BusTerminalBridge(),
        decoder=lambda value: value,
    )
    assert package["symbols"][0]["terminals"][0]["name"] == "IN<3:0>"
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/virtuoso_workbench/schemas/library-symbols-v1.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(package)
    target = tmp_path / "bus-terminal.json"
    write_package(target, package)
    arguments = argparse.Namespace(symbol_action="inspect", file=target)
    cli.execute_symbols(arguments, cli.Parser())
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert load_package(target)["symbols"][0]["terminals"][0]["name"] == "IN<3:0>"


@pytest.mark.parametrize("terminal_name", ["", "IN\n3:0", "IN\x00", "IN\x7f", "A" * 129])
def test_terminal_names_reject_control_and_oversize_input(terminal_name):
    package = build_package("generic", IDENTITY, [symbol()])
    package["symbols"][0]["terminals"][0]["name"] = terminal_name
    package["digest"] = content_digest(package)
    with pytest.raises(SymbolPackageError, match="terminal"):
        validate_package(package)
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/virtuoso_workbench/schemas/library-symbols-v1.json").read_text()
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(package)


@pytest.mark.parametrize(("library", "cells"), [("generic;bad", ["nmos"]), ("generic", ["nmos;bad"])])
def test_export_keeps_library_and_cell_skill_names_restricted(library, cells):
    with pytest.raises(SymbolPackageError, match="valid library"):
        export_package(
            library,
            cells,
            "local",
            65432,
            client_factory=lambda **_: pytest.fail("invalid names must not open Bridge"),
            decoder=lambda value: value,
        )


def test_failed_cli_export_never_creates_output(tmp_path, monkeypatch):
    bridge = FakeBridge()

    def failed_export(library, cells, host, port, view, source_version):
        return export_package(
            library,
            ["missingCell"],
            host,
            port,
            view,
            source_version,
            client_factory=lambda **_: bridge,
            decoder=lambda value: value,
        )

    monkeypatch.setattr(cli, "export_package", failed_export)
    target = tmp_path / "failed.json"
    arguments = argparse.Namespace(
        symbol_action="export",
        bridge_host="local",
        bridge_docker=None,
        bridge_port=65432,
        library="generic",
        cell=["missingCell"],
        view="symbol",
        source_version=None,
        output=target,
        force=False,
    )
    with pytest.raises(RequestError, match="missing"):
        cli.execute_symbols(arguments, argparse.ArgumentParser())
    assert not target.exists()
    assert all(not any(token in query for token in ("dbSave", "dbCreate", "cdfEvaluate")) for query in bridge.queries)


def test_failed_atomic_link_leaves_no_file_or_temporary_file(tmp_path, monkeypatch):
    package = build_package("generic", IDENTITY, [symbol()])

    def fail_link(*_):
        raise OSError("atomic link failed")

    monkeypatch.setattr(symbol_module.os, "link", fail_link)
    with pytest.raises(OSError, match="atomic link failed"):
        write_package(tmp_path / "atomic.json", package)
    assert list(tmp_path.iterdir()) == []


def test_force_replaces_relative_and_absolute_symlink_leaves(tmp_path, monkeypatch):
    package = build_package("generic", IDENTITY, [symbol()])
    relative_target = tmp_path / "relative-target.json"
    relative_target.write_bytes(b"relative target bytes")
    relative_link = tmp_path / "relative-package.json"
    relative_link.symlink_to(relative_target)
    absolute_target = tmp_path / "absolute-target.json"
    absolute_target.write_bytes(b"absolute target bytes")
    absolute_link = tmp_path / "absolute-package.json"
    absolute_link.symlink_to(absolute_target)
    monkeypatch.chdir(tmp_path)

    write_package(Path("relative-package.json"), package, force=True)
    write_package(absolute_link, package, force=True)

    assert not relative_link.is_symlink()
    assert not absolute_link.is_symlink()
    assert relative_target.read_bytes() == b"relative target bytes"
    assert absolute_target.read_bytes() == b"absolute target bytes"
    assert load_package(relative_link)["digest"] == package["digest"]
    assert load_package(absolute_link)["digest"] == package["digest"]


def test_no_force_refuses_symlinks_and_preserves_referents(tmp_path, monkeypatch):
    package = build_package("generic", IDENTITY, [symbol()])
    monkeypatch.chdir(tmp_path)
    for name in ("relative", "absolute"):
        target = tmp_path / f"{name}-target.json"
        content = f"{name} target bytes".encode()
        target.write_bytes(content)
        link = tmp_path / f"{name}-package.json"
        link.symlink_to(target)
        path = Path(link.name) if name == "relative" else link
        with pytest.raises(SymbolPackageError, match="exists"):
            write_package(path, package)
        assert link.is_symlink()
        assert target.read_bytes() == content


def test_no_force_refuses_dangling_symlink_without_creating_referent(tmp_path, monkeypatch):
    package = build_package("generic", IDENTITY, [symbol()])
    target = tmp_path / "missing-target.json"
    link = tmp_path / "dangling-package.json"
    link.symlink_to(target)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SymbolPackageError, match="exists"):
        write_package(Path(link.name), package)

    assert link.is_symlink()
    assert not target.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_force_replaces_dangling_symlink_leaf(tmp_path):
    package = build_package("generic", IDENTITY, [symbol()])
    target = tmp_path / "missing-target.json"
    link = tmp_path / "dangling-package.json"
    link.symlink_to(target)

    write_package(link, package, force=True)

    assert not link.is_symlink()
    assert not target.exists()
    assert load_package(link)["digest"] == package["digest"]


def test_presentation_metadata_does_not_change_revision(demo):
    annotated = json.loads(json.dumps(demo))
    annotated["instances"][0]["master_view"] = "symbol"
    annotated["instances"][0]["library_identity"] = IDENTITY
    annotated["instances"][0]["symbol_metadata_error"] = "unsupported-magnification"
    annotated["instances"][0]["terminals"][0]["source_pin_anchors"] = [[-4, 6.7], [-3.5, 6.7]]
    assert server.revision(annotated) == server.revision(demo)


def test_symbol_metadata_requires_name_library_cell_and_unit_scale(demo):
    instance = copy.deepcopy(demo["instances"][0])
    snapshot = {"instances": [instance]}
    anchors = [[terminal["name"], [terminal["xy"]]] for terminal in instance["terminals"]]
    row = [
        instance["name"],
        instance["library"],
        instance["master"],
        "symbol",
        "/synthetic/library",
        1.0,
        None,
        anchors,
    ]
    server._attach_symbol_metadata(snapshot, [row])
    assert instance["master_view"] == "symbol"
    assert instance["library_identity"].startswith("sha256:")
    assert all(terminal["source_pin_anchors"] == [terminal["xy"]] for terminal in instance["terminals"])
    for changed, reason in (
        ({"library": "other"}, "metadata-binding-mismatch"),
        ({"master": "other"}, "metadata-binding-mismatch"),
        ({"magnification": 2.0}, "unsupported-magnification"),
        ({"parameterized": True}, "unsupported-parameterized-variant"),
    ):
        candidate = copy.deepcopy(demo["instances"][0])
        candidate.pop("library_identity", None)
        candidate.pop("master_view", None)
        target = {"instances": [candidate]}
        changed_row = list(row)
        changed_row[1] = changed.get("library", changed_row[1])
        changed_row[2] = changed.get("master", changed_row[2])
        changed_row[5] = changed.get("magnification", changed_row[5])
        changed_row[6] = changed.get("parameterized", changed_row[6])
        server._attach_symbol_metadata(target, [changed_row])
        assert candidate["symbol_metadata_error"] == reason
        assert "library_identity" not in candidate
        assert not any("source_pin_anchors" in terminal for terminal in candidate["terminals"])
    assert "dbTransformBBox" in server.SYMBOL_METADATA_QUERY
    assert "term~>pins" in server.SYMBOL_METADATA_QUERY and "pin~>figs" in server.SYMBOL_METADATA_QUERY
    assert "master~>superMaster" in server.SYMBOL_METADATA_QUERY


def _skill_parentheses_balanced(source):
    depth = 0
    quoted = False
    escaped = False
    for char in source:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quoted


def _skill_parenthesis_depth_at(source, marker):
    depth = 0
    quoted = False
    escaped = False
    for char in source[: source.index(marker)]:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
    return depth


def test_symbol_metadata_query_is_balanced_and_closes_pin_loops():
    query = server.SYMBOL_METADATA_QUERY.replace("@LIB@", "generic").replace("@CELL@", "nmos")
    assert _skill_parentheses_balanced('list(") (", 1)')
    assert _skill_parentheses_balanced(query)
    assert _skill_parenthesis_depth_at(query, "anchors=nil") == _skill_parenthesis_depth_at(query, "termRows=cons")


def test_demo_revision_is_unchanged_by_presentation_metadata(demo):
    original = server.revision(demo)
    assert original == "sha256:9dc6c44a3ae6b4603fa36ec0326da3be0bf1bb8adbbd1daee2ca7543e87c1200"
    annotated = copy.deepcopy(demo)
    annotated["instances"][0]["library_identity"] = "sha256:" + "0" * 64
    annotated["instances"][0]["master_view"] = "symbol"
    annotated["instances"][0]["symbol_metadata_error"] = "none"
    annotated["instances"][0]["terminals"][0]["source_pin_anchors"] = [[0, 0], [0.25, 0]]
    assert server.revision(annotated) == original


def test_read_snapshot_attaches_matching_fake_bridge_anchors_and_rejects_stale_binding(monkeypatch):
    class FakeClient:
        def __init__(self, metadata):
            self.metadata = metadata
            self.queries = []

        @classmethod
        def local(cls, **_):
            return cls('(("X" "generic" "nmos" "symbol" "/synthetic/library" 1.0 nil (("D" ((0 1) (0.25 1))))))')

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute_skill(self, query, **_):
            self.queries.append(query)
            if len(self.queries) == 1:
                return Result("SNAPSHOT\nI|X|generic|nmos|R0|(0 1)|((-1 -1) (1 1))\nT|D|N|(0 1)\nEND")
            return Result(self.metadata)

    module = types.ModuleType("virtuoso_bridge")
    module.VirtuosoClient = FakeClient
    module.decode_skill_output = lambda value: value
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", module)
    matching = server.read_snapshot("examples", "test", "local", 65432, include_symbol_metadata=True)
    instance = matching["instances"][0]
    assert instance["master_view"] == "symbol"
    assert instance["terminals"][0]["source_pin_anchors"] == [[0.0, 1.0], [0.25, 1.0]]
    stale_module = types.ModuleType("virtuoso_bridge")

    class StaleClient(FakeClient):
        @classmethod
        def local(cls, **_):
            return cls('(("X" "generic" "different-cell" "symbol" "/synthetic/library" 1.0 nil (("D" ((0 1))))))')

    stale_module.VirtuosoClient = StaleClient
    stale_module.decode_skill_output = lambda value: value
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", stale_module)
    stale = server.read_snapshot("examples", "test", "local", 65432, include_symbol_metadata=True)["instances"][0]
    assert stale["symbol_metadata_error"] == "metadata-binding-mismatch"
    assert "library_identity" not in stale and "master_view" not in stale

    class VariantClient(FakeClient):
        @classmethod
        def local(cls, **_):
            return cls('(("X" "generic" "nmos" "symbol" "/synthetic/library" 1.0 t (("D" ((0 1))))))')

    variant_module = types.ModuleType("virtuoso_bridge")
    variant_module.VirtuosoClient = VariantClient
    variant_module.decode_skill_output = lambda value: value
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", variant_module)
    variant = server.read_snapshot("examples", "test", "local", 65432, include_symbol_metadata=True)["instances"][0]
    assert variant["symbol_metadata_error"] == "unsupported-parameterized-variant"
    assert "library_identity" not in variant

    class RaisingClient(FakeClient):
        @classmethod
        def local(cls, **_):
            return cls("")

        def execute_skill(self, query, **kwargs):
            if not self.queries:
                return super().execute_skill(query, **kwargs)
            self.queries.append(query)
            raise RuntimeError("synthetic metadata query failure")

    raising_module = types.ModuleType("virtuoso_bridge")
    raising_module.VirtuosoClient = RaisingClient
    raising_module.decode_skill_output = lambda value: value
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", raising_module)
    unavailable = server.read_snapshot("examples", "test", "local", 65432, include_symbol_metadata=True)
    assert unavailable["cell"] == "test"
    assert unavailable["instances"][0]["symbol_metadata_error"] == "metadata-unavailable"


def test_read_snapshot_decodes_metadata_wrapper_and_keeps_snapshot_decoder(monkeypatch):
    snapshot_output = "SNAPSHOT\nI|X|generic|nmos|R0|(0 1)|((-1 -1) (1 1))\nT|D|N|(0 1)\nEND"
    metadata_rows = [["X", "generic", "nmos", "symbol", "/synthetic/library", 1.0, None, [["D", [[0, 1], [0.25, 1]]]]]]
    decoder_inputs = []

    def legacy_decoder(raw):
        decoder_inputs.append(raw)
        return _legacy_bridge_decoder(raw)

    class WrappedMetadataClient:
        def __init__(self):
            self.queries = []

        @classmethod
        def local(cls, **_):
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute_skill(self, query, **_):
            self.queries.append(query)
            if len(self.queries) == 1:
                return Result(snapshot_output)
            return Result(_bridge_transport_output(metadata_rows))

    module = types.ModuleType("virtuoso_bridge")
    module.VirtuosoClient = WrappedMetadataClient
    module.decode_skill_output = legacy_decoder
    monkeypatch.setitem(sys.modules, "virtuoso_bridge", module)

    snapshot = server.read_snapshot("examples", "test", "local", 65432, include_symbol_metadata=True)

    instance = snapshot["instances"][0]
    assert instance["master_view"] == "symbol"
    assert instance["terminals"][0]["source_pin_anchors"] == [[0.0, 1.0], [0.25, 1.0]]
    assert decoder_inputs == [snapshot_output]
