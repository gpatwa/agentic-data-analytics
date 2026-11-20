"""Deterministic schema facts the drafter reasons over (auto-ontology milestone 1).

Facts hold names, types, keys and comments only. There is no sample-value field on purpose: sampling and redaction arrive in
milestone 2, under the rule approved on 2026-11-14 (see docs/AUTO_ONTOLOGY_DESIGN.md). Nothing here touches a database.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SemanticType = Literal["string", "integer", "decimal", "boolean", "date", "timestamp"]

# Where a table's key came from. `declared`: a constraint in the warehouse catalog (informational on Snowflake and Databricks, so a claim,
# not an enforced guarantee). `profiled_full`: unique and non-null in the profile, which saw the whole table; a fact about today's rows.
# `none`: no key. `tested` is reserved for a later importer of dbt-style tests and is not accepted yet.
KeyEvidence = Literal["declared", "profiled_full", "none"]

# The sensitive-name list from the approved redaction rule (docs/AUTO_ONTOLOGY_DESIGN.md). A column whose name has one of these
# tokens is classified `restricted` in a draft and is never offered as a dimension or a measure. Reviewers can add it by hand.
# Amended by the owner on 2026-11-16 ("Yes, extend it"): birth, birthday and birthdate, so date_of_birth and birthDate match.
# Matching is by whole token, so a column such as birth_rate is also excluded (conservative on purpose).
SENSITIVE_TOKENS = frozenset(
    {"email", "phone", "name", "address", "ssn", "tax", "dob", "ip", "token", "password", "secret", "key", "card",
     "account", "iban", "birth", "birthday", "birthdate"}
)

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactColumn(_Frozen):
    name: str = Field(min_length=1, max_length=255)
    data_type: SemanticType
    nullable: bool = True
    comment: str | None = Field(default=None, max_length=2_000)
    # Set from an upstream sensitivity tag (E6d). It can only add a restriction: nothing upstream can clear the name-based rule.
    restricted: bool = False


class FactForeignKey(_Frozen):
    columns: tuple[str, ...] = Field(min_length=1)
    ref_table: str = Field(min_length=1, max_length=255)
    ref_columns: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def same_length(self) -> "FactForeignKey":
        if len(self.columns) != len(self.ref_columns):
            raise ValueError("foreign key column lists must have the same length")
        return self


class FactTable(_Frozen):
    name: str = Field(min_length=1, max_length=255)
    asset_id: str = Field(min_length=1, max_length=512)
    comment: str | None = Field(default=None, max_length=2_000)
    columns: tuple[FactColumn, ...] = Field(min_length=1)
    primary_key: tuple[str, ...] = ()
    key_evidence: KeyEvidence = "none"
    foreign_keys: tuple[FactForeignKey, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def default_key_evidence(cls, data):
        """A key given without a stated source is a declared one (that is how every earlier caller built it)."""
        if isinstance(data, dict) and data.get("primary_key") and "key_evidence" not in data:
            return {**data, "key_evidence": "declared"}
        return data

    @model_validator(mode="after")
    def keys_exist(self) -> "FactTable":
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate column names in table {self.name}")
        known = set(names)
        for key in (self.primary_key, *(fk.columns for fk in self.foreign_keys)):
            if not set(key) <= known:
                raise ValueError(f"key references an unknown column in table {self.name}")
        if bool(self.primary_key) != (self.key_evidence != "none"):
            raise ValueError(f"key and key evidence disagree in table {self.name}")
        return self

    def column(self, name: str) -> FactColumn | None:
        return next((column for column in self.columns if column.name == name), None)


class SchemaFacts(_Frozen):
    tables: tuple[FactTable, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_tables(self) -> "SchemaFacts":
        names = [table.name for table in self.tables]
        if len(names) != len(set(names)):
            raise ValueError("duplicate table names")
        return self

    def table(self, name: str) -> FactTable | None:
        return next((table for table in self.tables if table.name == name), None)

    def fingerprint(self) -> str:
        """SHA-256 over the canonical JSON of the facts, so an unchanged schema yields an unchanged fingerprint."""
        dumped = self.model_dump(mode="json")
        for table in dumped["tables"]:  # only a profiled key is written, so facts that carried no evidence label keep their fingerprint
            if table["key_evidence"] != "profiled_full":
                del table["key_evidence"]
            for column in table["columns"]:
                if not column["restricted"]:  # likewise, a column is marked only when an upstream tag restricted it
                    del column["restricted"]
        canonical = json.dumps(dumped, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_restricted(column: FactColumn) -> bool:
    return column.restricted or is_sensitive_name(column.name)


def is_sensitive_name(name: str) -> bool:
    tokens = {token for token in _TOKEN_SPLIT.split(_CAMEL.sub("_", name).lower()) if token}
    return bool(tokens & SENSITIVE_TOKENS)


_TYPE_RULES: tuple[tuple[re.Pattern[str], SemanticType], ...] = (
    (re.compile(r"^(tinyint|smallint|int|integer|bigint|serial|bigserial|smallserial)\b"), "integer"),
    (re.compile(r"^(numeric|decimal|number|real|float|double|money)\b"), "decimal"),
    (re.compile(r"^(bool|boolean|bit)\b"), "boolean"),
    (re.compile(r"^timestamp|^datetime"), "timestamp"),
    (re.compile(r"^date\b"), "date"),
    (re.compile(r"^(varchar|character|char|text|string|uuid|citext|nvarchar|nchar)"), "string"),
)


def normalize_type(raw: str) -> SemanticType | None:
    """Map a physical type name to a semantic type, or None when we do not recognise it (the column is then skipped)."""
    lowered = raw.strip().lower()
    for pattern, semantic in _TYPE_RULES:
        if pattern.search(lowered):
            return semantic
    return None
