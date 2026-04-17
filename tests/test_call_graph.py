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
