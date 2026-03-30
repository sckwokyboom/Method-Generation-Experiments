from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new",
    "package", "private", "protected", "public", "return", "short", "static",
    "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "var", "yield", "record",
    "sealed", "permits",
})

_PLACEHOLDER = "_"

# Node types that represent literals to strip.
_LITERAL_TYPES = frozenset({
    "decimal_integer_literal",
    "hex_integer_literal",
    "octal_integer_literal",
    "binary_integer_literal",
    "decimal_floating_point_literal",
    "hex_floating_point_literal",
    "string_literal",
    "character_literal",
    "true",
    "false",
    "null_literal",
})

# Parent types where an identifier is always a declaration/type name — keep it.
_KEEP_PARENT_TYPES = frozenset({
    "annotation",
    "marker_annotation",
    "scoped_identifier",
    "import_declaration",
    "package_declaration",
    "type_parameter",
})

# (parent_type, field_name) pairs where an identifier should be KEPT.
_KEEP_PARENT_FIELD = frozenset({
    ("method_invocation", "name"),
    ("method_invocation", "object"),
    ("super_method_invocation", "name"),
    ("method_declaration", "name"),
    ("class_declaration", "name"),
    ("enum_declaration", "name"),
    ("interface_declaration", "name"),
    ("annotation_type_declaration", "name"),
    ("field_access", "field"),
    ("field_access", "object"),
    ("constructor_declaration", "name"),
    ("enum_constant", "name"),
})


def _get_field_name(parent, node) -> str | None:
    """Get the field name this node occupies in its parent."""
    for i, child in enumerate(parent.children):
        if child.id == node.id:
            return parent.field_name_for_child(i)
    return None


def _is_variable_identifier(node) -> bool:
    """Return True if this identifier node refers to a variable (not a type or method name)."""
    if node.type == "type_identifier":
        return False
    if node.type != "identifier":
        return False

    parent = node.parent
    if parent is None:
        return True

    parent_type = parent.type

    # Always keep identifiers in these parent types.
    if parent_type in _KEEP_PARENT_TYPES:
        return False

    # Check (parent_type, field_name) pairs.
    field = _get_field_name(parent, node)
    if (parent_type, field) in _KEEP_PARENT_FIELD:
        return False

    # Type field always means a type reference — keep.
    if field == "type":
        return False

    return True


def strip_identifiers_and_literals(java_code: str) -> str:
    """Strip variable identifiers and literals from Java code using tree-sitter.

    Preserves type names, method names, and keywords. Replaces stripped tokens
    with a placeholder. Falls back to regex on parse failure.
    """
    try:
        return _strip_with_tree_sitter(java_code)
    except Exception:
        log.debug("tree-sitter parse failed, falling back to regex", exc_info=True)
        return _strip_with_regex(java_code)


def _strip_with_tree_sitter(java_code: str) -> str:
    import tree_sitter_java as tsjava
    from tree_sitter import Language, Parser

    language = Language(tsjava.language())
    parser = Parser()
    parser.set_language(language)

    source = java_code.encode("utf-8")
    tree = parser.parse(source)

    # Collect byte ranges to replace (start, end).
    replacements: list[tuple[int, int]] = []

    def _walk(node):
        if node.type in _LITERAL_TYPES:
            replacements.append((node.start_byte, node.end_byte))
            return  # Don't recurse into literal children.

        if _is_variable_identifier(node):
            replacements.append((node.start_byte, node.end_byte))
            return

        for child in node.children:
            _walk(child)

    _walk(tree.root_node)

    # Sort by position and rebuild string, replacing matched ranges.
    replacements.sort()
    parts: list[str] = []
    prev_end = 0
    for start, end in replacements:
        if start < prev_end:
            continue  # Overlapping range, skip.
        parts.append(source[prev_end:start].decode("utf-8", errors="replace"))
        parts.append(_PLACEHOLDER)
        prev_end = end
    parts.append(source[prev_end:].decode("utf-8", errors="replace"))

    return "".join(parts)


def _strip_with_regex(java_code: str) -> str:
    """Regex fallback: strip string/char literals, numbers, and non-keyword lowercase identifiers."""
    # Strip string and char literals.
    code = re.sub(r'"(?:[^"\\]|\\.)*"', _PLACEHOLDER, java_code)
    code = re.sub(r"'(?:[^'\\]|\\.)*'", _PLACEHOLDER, code)
    # Strip numeric literals.
    code = re.sub(r"\b(?:0[xXbB])?[0-9][0-9a-fA-F_]*\.?[0-9a-fA-F_]*[fFdDlL]?\b", _PLACEHOLDER, code)

    # Strip non-keyword lowercase identifiers (heuristic for variable names).
    def _replace_ident(m: re.Match) -> str:
        word = m.group(0)
        return _PLACEHOLDER if word not in _JAVA_KEYWORDS else word

    code = re.sub(r"\b[a-z_]\w*\b", _replace_ident, code)
    return code
