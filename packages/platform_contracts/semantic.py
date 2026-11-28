"""Versioned, catalog-neutral semantic contracts for governed analytics.

These models describe certified analytical meaning without embedding warehouse
SQL. A later registry resolves versions, an intent planner selects IDs, and a
compiler turns only validated contracts into dialect-specific SQL.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from packages.platform_contracts.semantic_terms import (
    MAX_SYNONYMS,
    contains_phrase,
    normalize_term,
    synonym_problem,
)

SEMANTIC_CONTRACT_VERSION = "v1"


def _check_synonyms(values: list[str]) -> list[str]:
    if len(values) > MAX_SYNONYMS:
        raise ValueError("synonym_count")
    problem = next((p for p in map(synonym_problem, values) if p), None)
    if problem:
        raise ValueError(problem)
    keys = [normalize_term(v) for v in values]
    if len(keys) != len(set(keys)):
        raise ValueError("synonym_duplicate")
    return values


class _Worded(BaseModel):
    """Optional certified business words. Empty by default, and left out of a dump when empty, so a contract without them hashes as before."""

    synonyms: list[str] = Field(default_factory=list)

    @field_validator("synonyms")
    @classmethod
    def _synonyms(cls, value: list[str]) -> list[str]:
        return _check_synonyms(value)

    @model_serializer(mode="wrap")
    def _omit_empty_words(self, handler):
        data = handler(self)
        if not data.get("synonyms", True):
            data.pop("synonyms", None)
        if "display_name" in data and data["display_name"] is None:
            data.pop("display_name")
        return data


class SemanticOwner(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    owner_type: Literal["team", "person", "service"]
    contact_reference: str | None = Field(default=None, max_length=512)


class SemanticDataset(_Worded):
    id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    source_asset_id: str = Field(min_length=1, max_length=512)
    physical_name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2_000)
    owner_ids: list[str] = Field(min_length=1)


class SemanticField(_Worded):
    id: str = Field(min_length=1, max_length=255)
    dataset_id: str = Field(min_length=1, max_length=255)
    physical_name: str = Field(min_length=1, max_length=255)
    data_type: Literal["string", "integer", "decimal", "boolean", "date", "timestamp"]
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"


class SemanticGrain(BaseModel):
    kind: Literal["row", "entity", "transaction", "order", "item", "custom"]
    key_field_ids: list[str] = Field(min_length=1)


class SemanticEntity(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    dataset_id: str = Field(min_length=1, max_length=255)
    grain: SemanticGrain
    owner_ids: list[str] = Field(min_length=1)


class SemanticDimension(_Worded):
    id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    dataset_id: str = Field(min_length=1, max_length=255)
    field_id: str = Field(min_length=1, max_length=255)
    entity_id: str | None = Field(default=None, max_length=255)
    dimension_type: Literal["categorical", "numeric", "temporal", "identifier"]
    owner_ids: list[str] = Field(min_length=1)


class SemanticMetric(_Worded):
    id: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    dataset_id: str = Field(min_length=1, max_length=255)
    aggregation: Literal["sum", "average", "count", "count_distinct", "min", "max", "ratio"]
    measure_field_id: str | None = Field(default=None, max_length=255)
    numerator_metric_id: str | None = Field(default=None, max_length=255)
    denominator_metric_id: str | None = Field(default=None, max_length=255)
    grain: SemanticGrain
    required_filter_ids: list[str] = Field(default_factory=list)
    certification: Literal["certified", "candidate"] = "candidate"
    owner_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_formula_shape(self) -> "SemanticMetric":
        if self.aggregation == "ratio":
            if not self.numerator_metric_id or not self.denominator_metric_id:
                raise ValueError("ratio metrics require numerator_metric_id and denominator_metric_id")
            if self.measure_field_id:
                raise ValueError("ratio metrics cannot define measure_field_id")
        elif self.aggregation == "count":
            if self.numerator_metric_id or self.denominator_metric_id:
                raise ValueError("count metrics cannot reference numerator or denominator metrics")
        elif self.aggregation == "count_distinct" and not self.measure_field_id:
            raise ValueError("count_distinct metrics require measure_field_id")
        elif not self.measure_field_id:
            raise ValueError(f"{self.aggregation} metrics require measure_field_id")
        return self


class SemanticJoin(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    from_dataset_id: str = Field(min_length=1, max_length=255)
    to_dataset_id: str = Field(min_length=1, max_length=255)
    from_field_ids: list[str] = Field(min_length=1)
    to_field_ids: list[str] = Field(min_length=1)
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
    approved: bool = False

    @model_validator(mode="after")
    def validate_key_pairs(self) -> "SemanticJoin":
        if len(self.from_field_ids) != len(self.to_field_ids):
            raise ValueError("join field lists must have the same length")
        if self.from_dataset_id == self.to_dataset_id:
            raise ValueError("joins must connect two distinct datasets")
        return self


class SemanticFilter(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    dataset_id: str = Field(min_length=1, max_length=255)
    field_id: str = Field(min_length=1, max_length=255)
    operator: Literal["equals", "not_equals", "in", "not_in", "greater_than", "less_than"]
    value_source: Literal["literal", "request_context", "identity_claim"]
    literal_value: bool | int | float | str | list[bool | int | float | str] | None = None

    @model_validator(mode="after")
    def validate_value_source(self) -> "SemanticFilter":
        if self.value_source == "literal" and self.literal_value is None:
            raise ValueError("literal filters require literal_value")
        if self.value_source != "literal" and self.literal_value is not None:
            raise ValueError("context and identity filters cannot define literal_value")
        return self


class SemanticPolicy(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    target_ids: list[str] = Field(min_length=1)
    classification: Literal["public", "internal", "confidential", "restricted"]
    allowed_purposes: list[str] = Field(min_length=1)
    required_filter_ids: list[str] = Field(default_factory=list)
    owner_ids: list[str] = Field(min_length=1)
    # Groups entitled to confidential or restricted targets. Membership in *any* group used to be enough; now it must be one of these.
    allowed_groups: list[str] = Field(default_factory=list)


class SemanticContract(BaseModel):
    """A self-contained, cross-reference-validated semantic contract version."""

    contract_version: Literal[SEMANTIC_CONTRACT_VERSION] = SEMANTIC_CONTRACT_VERSION
    id: str = Field(min_length=1, max_length=255)
    tenant_id: str = Field(min_length=1, max_length=255)
    domain: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    owners: list[SemanticOwner] = Field(min_length=1)
    datasets: list[SemanticDataset] = Field(min_length=1)
    fields: list[SemanticField] = Field(min_length=1)
    entities: list[SemanticEntity] = Field(default_factory=list)
    dimensions: list[SemanticDimension] = Field(default_factory=list)
    metrics: list[SemanticMetric] = Field(default_factory=list)
    joins: list[SemanticJoin] = Field(default_factory=list)
    filters: list[SemanticFilter] = Field(default_factory=list)
    policies: list[SemanticPolicy] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> "SemanticContract":
        owner_ids = _unique_ids("owner", self.owners)
        dataset_ids = _unique_ids("dataset", self.datasets)
        field_ids = _unique_ids("field", self.fields)
        entity_ids = _unique_ids("entity", self.entities)
        dimension_ids = _unique_ids("dimension", self.dimensions)
        metric_ids = _unique_ids("metric", self.metrics)
        _unique_ids("join", self.joins)
        filter_ids = _unique_ids("filter", self.filters)
        _unique_ids("policy", self.policies)
        _require_unique_semantic_asset_ids(
            self.datasets, self.fields, self.entities, self.dimensions, self.metrics
        )

        for dataset in self.datasets:
            _require_references("dataset owner", dataset.owner_ids, owner_ids)
        for field in self.fields:
            _require_references("field dataset", [field.dataset_id], dataset_ids)
        for entity in self.entities:
            _require_references("entity dataset", [entity.dataset_id], dataset_ids)
            _require_references("entity owner", entity.owner_ids, owner_ids)
            _require_fields_for_dataset("entity grain", entity.grain.key_field_ids, field_ids, self.fields, entity.dataset_id)
        for dimension in self.dimensions:
            _require_references("dimension dataset", [dimension.dataset_id], dataset_ids)
            _require_references("dimension field", [dimension.field_id], field_ids)
            _require_references("dimension owner", dimension.owner_ids, owner_ids)
            _require_fields_for_dataset("dimension", [dimension.field_id], field_ids, self.fields, dimension.dataset_id)
            if dimension.entity_id:
                _require_references("dimension entity", [dimension.entity_id], entity_ids)
                entity = _find_by_id(self.entities, dimension.entity_id)
                if entity.dataset_id != dimension.dataset_id:
                    raise ValueError("dimension entity must belong to the dimension dataset")
        for metric in self.metrics:
            _require_references("metric dataset", [metric.dataset_id], dataset_ids)
            _require_references("metric owner", metric.owner_ids, owner_ids)
            _require_references("metric filter", metric.required_filter_ids, filter_ids)
            _require_fields_for_dataset("metric grain", metric.grain.key_field_ids, field_ids, self.fields, metric.dataset_id)
            if metric.measure_field_id:
                _require_fields_for_dataset("metric measure", [metric.measure_field_id], field_ids, self.fields, metric.dataset_id)
            if metric.aggregation == "ratio":
                _require_references("ratio numerator", [metric.numerator_metric_id], metric_ids)
                _require_references("ratio denominator", [metric.denominator_metric_id], metric_ids)
        for join in self.joins:
            _require_references("join source dataset", [join.from_dataset_id], dataset_ids)
            _require_references("join target dataset", [join.to_dataset_id], dataset_ids)
            _require_fields_for_dataset("join source", join.from_field_ids, field_ids, self.fields, join.from_dataset_id)
            _require_fields_for_dataset("join target", join.to_field_ids, field_ids, self.fields, join.to_dataset_id)
        for semantic_filter in self.filters:
            _require_references("filter dataset", [semantic_filter.dataset_id], dataset_ids)
            _require_fields_for_dataset("filter", [semantic_filter.field_id], field_ids, self.fields, semantic_filter.dataset_id)
        _check_term_rules(self)
        target_ids = dataset_ids | field_ids | entity_ids | dimension_ids | metric_ids
        for policy in self.policies:
            _require_references("policy target", policy.target_ids, target_ids)
            _require_references("policy filter", policy.required_filter_ids, filter_ids)
            _require_references("policy owner", policy.owner_ids, owner_ids)
        return self


def _check_term_rules(contract: "SemanticContract") -> None:
    """Rules across objects: a business word must name one thing in a kind, and must not be a whole-word part of another thing's name.

    Only explicit words (a display name or a synonym) are checked against the other objects' names. The ids of existing contracts
    already overlap in places (a `revenue` metric beside `revenue_per_order`), and that is left as certified.
    """
    kinds: dict[str, list[tuple[str, list[tuple[str, bool]]]]] = {}
    for kind, items in (("dataset", contract.datasets), ("metric", contract.metrics), ("dimension", contract.dimensions), ("field", contract.fields)):
        rows = []
        for item in items:
            terms = [(normalize_term(item.id), False)]
            if kind in ("metric", "dimension") and item.display_name:  # a dataset's display name predates this rule and stays as certified
                terms.append((normalize_term(item.display_name), True))
            terms += [(normalize_term(v), True) for v in item.synonyms]
            rows.append((item.id, [t for t in terms if t[0]]))
        kinds[kind] = rows
    all_ids = {normalize_term(i) for rows in kinds.values() for i, _ in rows}
    for kind, rows in kinds.items():
        for index, (item_id, terms) in enumerate(rows):
            own_id = normalize_term(item_id)
            for term, explicit in terms:
                if explicit and term != own_id and term in all_ids - {own_id}:
                    raise ValueError("synonym_is_another_id")
            for other_id, other_terms in rows[index + 1 :]:
                for term, explicit in terms:
                    for other, other_explicit in other_terms:
                        if (explicit or other_explicit) and (contains_phrase(term, other) or contains_phrase(other, term)):
                            raise ValueError("synonym_overlaps_another_term")


class SemanticRegistryDocument(BaseModel):
    """A Git-tracked semantic contract with its registry lifecycle state."""

    lifecycle: Literal["draft", "certified", "deprecated"]
    contract: SemanticContract


def _unique_ids(kind: str, values: list[BaseModel]) -> set[str]:
    ids = [value.id for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {kind} IDs are not allowed")
    return set(ids)


def _require_references(kind: str, references: list[str | None], valid_ids: set[str]) -> None:
    missing = sorted(reference for reference in references if reference and reference not in valid_ids)
    if missing:
        raise ValueError(f"unknown {kind} reference(s): {', '.join(missing)}")


def _require_unique_semantic_asset_ids(*groups: list[BaseModel]) -> None:
    ids = [value.id for group in groups for value in group]
    if len(ids) != len(set(ids)):
        raise ValueError("semantic asset IDs must be unique across asset types")


def _require_fields_for_dataset(
    kind: str,
    references: list[str],
    field_ids: set[str],
    fields: list[SemanticField],
    dataset_id: str,
) -> None:
    _require_references(kind, references, field_ids)
    for reference in references:
        field = _find_by_id(fields, reference)
        if field.dataset_id != dataset_id:
            raise ValueError(f"{kind} field {reference} must belong to dataset {dataset_id}")


def _find_by_id(values: list[BaseModel], identifier: str) -> BaseModel:
    return next(value for value in values if value.id == identifier)
