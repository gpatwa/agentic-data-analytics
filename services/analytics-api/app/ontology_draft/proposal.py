"""The only shape a model may return, and a tolerant parser for it.

The shape has no field that can carry SQL, and every item uses `extra="forbid"`, so a stray `sql` or `query` key makes that item
malformed. Each item is parsed on its own: one bad item is dropped with a reason and cannot sink the valid ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Identifier = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
MAX_ITEMS_PER_LIST = 200
LIST_KEYS = ("datasets", "metrics", "dimensions", "joins", "glossary")


class DraftError(Exception):
    """The model call failed or its reply could not be read at all. Nothing is emitted."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DraftDatasetText(_Item):
    table: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=500)


class DraftMetric(_Item):
    id: str = Identifier
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    synonyms: list[str] = Field(default_factory=list, max_length=10)  # trimmed to the first 5 by the validator, which records each drop
    table: str = Field(min_length=1, max_length=255)
    aggregation: Literal["sum", "average", "count", "count_distinct", "min", "max"]
    measure_column: str | None = Field(default=None, max_length=255)
    grain_key_columns: list[str] = Field(min_length=1, max_length=5)
    description: str = Field(min_length=1, max_length=500)


class DraftDimension(_Item):
    id: str = Identifier
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    synonyms: list[str] = Field(default_factory=list, max_length=10)
    table: str = Field(min_length=1, max_length=255)
    column: str = Field(min_length=1, max_length=255)
    dimension_type: Literal["categorical", "numeric", "temporal"]
    description: str = Field(min_length=1, max_length=500)


class DraftJoin(_Item):
    from_table: str = Field(min_length=1, max_length=255)
    from_columns: list[str] = Field(min_length=1, max_length=5)
    to_table: str = Field(min_length=1, max_length=255)
    to_columns: list[str] = Field(min_length=1, max_length=5)
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]


class DraftTerm(_Item):
    term: str = Field(min_length=1, max_length=120)
    definition: str = Field(min_length=1, max_length=500)


class DraftEnvelope(BaseModel):
    """Used only to publish the JSON schema to the model; parsing goes through `parse_proposal`."""

    model_config = ConfigDict(extra="forbid")

    datasets: list[DraftDatasetText] = Field(default_factory=list)
    metrics: list[DraftMetric] = Field(default_factory=list)
    dimensions: list[DraftDimension] = Field(default_factory=list)
    joins: list[DraftJoin] = Field(default_factory=list)
    glossary: list[DraftTerm] = Field(default_factory=list)


_ITEM_TYPES: dict[str, type[_Item]] = {
    "datasets": DraftDatasetText,
    "metrics": DraftMetric,
    "dimensions": DraftDimension,
    "joins": DraftJoin,
    "glossary": DraftTerm,
}


@dataclass(frozen=True)
class Drop:
    """One candidate that did not survive, and why. `detail` never repeats model text or data values."""

    kind: str
    item: str
    code: str
    detail: str = ""


@dataclass(frozen=True)
class ParsedProposal:
    datasets: tuple[DraftDatasetText, ...]
    metrics: tuple[DraftMetric, ...]
    dimensions: tuple[DraftDimension, ...]
    joins: tuple[DraftJoin, ...]
    glossary: tuple[DraftTerm, ...]
    drops: tuple[Drop, ...]


def parse_proposal(raw: Any) -> ParsedProposal:
    """Read a model reply (a dict or a JSON string). Raise `DraftError` only when nothing usable can be read."""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise DraftError("unparseable_reply") from exc
    if not isinstance(raw, dict):
        raise DraftError("unparseable_reply")

    drops: list[Drop] = []
    parsed: dict[str, list[Any]] = {key: [] for key in LIST_KEYS}
    for index, key in enumerate(raw):
        if key not in LIST_KEYS:  # the key name is model text: report only its position
            drops.append(Drop("proposal", f"key#{index}", "unexpected_field"))
    for key in LIST_KEYS:
        items = raw.get(key, [])
        if not isinstance(items, list):
            drops.append(Drop(key, key, "malformed_list"))
            continue
        if len(items) > MAX_ITEMS_PER_LIST:
            drops.append(Drop(key, key, "too_many_items", f"kept the first {MAX_ITEMS_PER_LIST}"))
            items = items[:MAX_ITEMS_PER_LIST]
        for index, item in enumerate(items):
            try:
                parsed[key].append(_ITEM_TYPES[key].model_validate(item))
            except ValidationError as exc:
                fields = sorted({".".join(str(part) for part in error["loc"]) or "item" for error in exc.errors()})
                drops.append(Drop(key, f"#{index}", "malformed_item", "fields: " + ", ".join(fields)[:200]))
    return ParsedProposal(
        datasets=tuple(parsed["datasets"]),
        metrics=tuple(parsed["metrics"]),
        dimensions=tuple(parsed["dimensions"]),
        joins=tuple(parsed["joins"]),
        glossary=tuple(parsed["glossary"]),
        drops=tuple(drops),
    )
