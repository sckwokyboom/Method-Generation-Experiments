from __future__ import annotations

import re


def strip_comments(code: str) -> str:
    code = re.sub(r"//[^\n]*", "", code)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return code


def collapse_whitespace(code: str) -> str:
    lines = []
    for line in code.splitlines():
        stripped = " ".join(line.split())
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def normalize_code(code: str, identifier_unify: bool = False) -> str:
    code = strip_comments(code)
    code = collapse_whitespace(code)
    if identifier_unify:
        code = unify_identifiers(code)
    return code


def unify_identifiers(code: str) -> str:
    java_keywords = {
        "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
        "class", "const", "continue", "default", "do", "double", "else", "enum",
        "extends", "final", "finally", "float", "for", "goto", "if", "implements",
        "import", "instanceof", "int", "interface", "long", "native", "new",
        "package", "private", "protected", "public", "return", "short", "static",
        "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
        "transient", "try", "void", "volatile", "while", "var", "yield", "record",
        "sealed", "permits", "non-sealed", "true", "false", "null",
    }

    local_var_pattern = re.compile(
        r"\b(?:(?:final\s+)?(?:var|int|long|float|double|boolean|byte|char|short|String|"
        r"[A-Z]\w*(?:<[^>]*>)?))\s+(\w+)\s*[=;,)]"
    )

    mapping: dict[str, str] = {}
    counter = 0

    for match in local_var_pattern.finditer(code):
        name = match.group(1)
        if name not in mapping and name not in java_keywords and not name[0].isupper():
            counter += 1
            mapping[name] = f"var{counter}"

    if not mapping:
        return code

    def replace(m: re.Match) -> str:
        word = m.group(0)
        return mapping.get(word, word)

    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in mapping) + r")\b")
    return pattern.sub(replace, code)


def tokenize_code(code: str) -> list[str]:
    return re.findall(r"[a-zA-Z_]\w*|[0-9]+(?:\.[0-9]+)?|[^\s]", code)
