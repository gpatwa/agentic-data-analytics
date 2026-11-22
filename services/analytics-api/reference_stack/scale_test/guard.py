"""Only a plain SELECT on the target schema's own objects may leave this process during a scale test."""

from __future__ import annotations

import re

_COMMENT = re.compile(r"^\s*(/\*.*?\*/\s*)+", re.S)
_QUOTED = re.compile(r"`[^`]*`|'[^']*'")
_QUALIFIED = re.compile(r"`([^`]+)`\.`([^`]+)`\.`([^`]+)`")
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|GRANT|REVOKE|TRUNCATE|COPY|SET|CALL|USE|OPTIMIZE|VACUUM|REFRESH|EXPLAIN|DESCRIBE|SHOW)\b", re.I)


class ScaleGuardError(Exception):
    pass


def check_statement(sql: str, catalog: str, schema: str) -> None:
    body = _COMMENT.sub("", sql, count=1)
    if not re.match(r"\s*(SELECT|WITH)\b", body, re.I):
        raise ScaleGuardError("statement_not_select")
    if ";" in _QUOTED.sub("", body):
        raise ScaleGuardError("statement_has_semicolon")
    if _FORBIDDEN.search(_QUOTED.sub("", body)):
        raise ScaleGuardError("statement_has_forbidden_word")
    names = _QUALIFIED.findall(body)
    if not names:
        raise ScaleGuardError("statement_has_no_qualified_name")
    if any((c.casefold(), s.casefold()) != (catalog.casefold(), schema.casefold()) for c, s, _ in names):
        raise ScaleGuardError("statement_outside_schema")
