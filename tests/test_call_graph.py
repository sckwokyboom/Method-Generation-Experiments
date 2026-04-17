from __future__ import annotations

from pipeline.call_graph import canonical_vertex_id, canonicalize_invocation_signature


def test_canonical_vertex_id_normal_method():
    vid = canonical_vertex_id(
        class_fqn="com.example.Foo",
        method_name="bar",
        parameter_types=["java.lang.String", "int"],
        return_type="boolean",
    )
    assert vid == "com.example.Foo::bar(java.lang.String,int) -> boolean"


def test_canonical_vertex_id_no_params():
    vid = canonical_vertex_id(
        class_fqn="com.example.Foo",
        method_name="bar",
        parameter_types=[],
        return_type="void",
    )
    assert vid == "com.example.Foo::bar() -> void"


def test_canonical_vertex_id_constructor():
    vid = canonical_vertex_id(
        class_fqn="com.example.Foo",
        method_name="<init>",
        parameter_types=["java.lang.String"],
        return_type="com.example.Foo",
    )
    assert vid == "com.example.Foo::<init>(java.lang.String) -> com.example.Foo"


def test_canonicalize_invocation_signature_passthrough():
    sig = "com.example.Foo::bar(java.lang.String,int) -> boolean"
    assert canonicalize_invocation_signature(sig) == sig


def test_canonicalize_invocation_signature_normalizes_whitespace():
    sig = "com.example.Foo::bar(java.lang.String, int) ->  boolean"
    assert (
        canonicalize_invocation_signature(sig)
        == "com.example.Foo::bar(java.lang.String,int) -> boolean"
    )


import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "mini_extracted.json"


def _load_fixture():
    return json.loads(FIXTURE.read_text())


def test_flatten_vertices_includes_primary_and_siblings():
    from pipeline.call_graph import flatten_vertices

    vertices = flatten_vertices(_load_fixture())
    ids = {v["vertex_id"] for v in vertices}
    assert "com.example.Foo::bar() -> void" in ids
    assert "com.example.Foo::helper() -> int" in ids
    assert len(vertices) == 2  # deduplicated


def test_flatten_vertices_sibling_carries_class_fqn_and_filepath():
    from pipeline.call_graph import flatten_vertices

    vertices = {v["vertex_id"]: v for v in flatten_vertices(_load_fixture())}
    helper = vertices["com.example.Foo::helper() -> int"]
    assert helper["class_fqn"] == "com.example.Foo"
    assert helper["file_path"] == "src/main/java/com/example/Foo.java"
    assert helper["method_name"] == "helper"
    assert helper["return_type"] == "int"
    assert helper["parameter_types"] == []


def test_flatten_vertices_deduplicates_on_collision():
    from pipeline.call_graph import flatten_vertices

    vertices = flatten_vertices(_load_fixture())
    # Fixture contains Foo::bar both as primary AND as Foo::helper's sibling — must appear once.
    bars = [v for v in vertices if v["vertex_id"] == "com.example.Foo::bar() -> void"]
    assert len(bars) == 1
    # First-wins: invocation list should be the one from the primary entry (4 invocations), not the sibling (empty).
    assert len(bars[0]["invocations"]) == 4
