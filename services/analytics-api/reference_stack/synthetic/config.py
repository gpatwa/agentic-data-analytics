"""The generator's versioned configuration. Every rate is a parameter here, never a hidden constant: changing one bumps the digest.

The initial values are plausible guesses chosen by the authors. They were not measured from any real platform, and the manifest says so.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

GENERATOR_VERSION = "fd-1.0.0"
TIERS = {"T": 2_000, "S": 10_000, "M": 1_000_000, "L": 50_000_000, "XL": 1_000_000_000}  # T is for the fastest tests only
DEFECTS = ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10")

# How well each defect's real analogue is evidenced (docs/design/SYNTHETIC_DATA_GENERATOR_SPEC.md section 4.3). Reports show the groups apart.
EVIDENCE = {"D1": "reviewed", "D2": "reviewed", "D3": "partly_reviewed", "D4": "general", "D5": "general", "D6": "general",
            "D7": "partly_reviewed", "D8": "partly_reviewed", "D9": "general", "D10": "general"}
DEFECT_TEXT = {
    "D1": "cancellations and refunds are separate reversal rows with negative amounts",
    "D2": "cash tips are not recorded in the tip field",
    "D3": "late-arriving versions: an order is re-recorded with a corrected status",
    "D4": "duplicate events: an identical row written twice",
    "D5": "order_ts is stored in UTC while the business day is local",
    "D6": "mixed currencies in one amount column",
    "D7": "nulls and placeholder values (-1) in minutes columns",
    "D8": "cuisine spelling drift",
    "D9": "test orders at a test restaurant with 0.01 amounts",
    "D10": "orphan restaurant references",
}


class Rates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cancelled: float = Field(default=0.06, ge=0, le=0.5)
    refunded: float = Field(default=0.025, ge=0, le=0.5)
    partially_refunded: float = Field(default=0.015, ge=0, le=0.5)
    late_version: float = Field(default=0.7, ge=0, le=1)  # of orders whose final status is not delivered (D3)
    duplicate: float = Field(default=0.003, ge=0, le=0.2)  # of final non-reversal rows (D4)
    test_order: float = Field(default=0.0005, ge=0, le=0.05)  # (D9)
    orphan: float = Field(default=0.002, ge=0, le=0.2)  # of orders (D10)
    spelling_drift: float = Field(default=0.15, ge=0, le=1)  # of rows (D8)
    delivery_sentinel: float = Field(default=0.005, ge=0, le=0.2)  # (D7)
    prep_sentinel: float = Field(default=0.003, ge=0, le=0.2)  # (D7)
    cancelled_before_dispatch: float = Field(default=0.7, ge=0, le=1)
    promo: float = Field(default=0.2, ge=0, le=1)
    tip_card_or_wallet: float = Field(default=0.55, ge=0, le=1)
    tip_cash_true: float = Field(default=0.4, ge=0, le=1)  # tips really paid in cash, whether or not recorded


class GeneratorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: int = 20260101
    tier: str = "S"
    start_date: str = "2025-01-01"
    days: int = Field(default=365, ge=28, le=1096)
    rates: Rates = Rates()
    defects: tuple[str, ...] = DEFECTS  # a defect left out is switched off (used to prove the checks can fail)

    @model_validator(mode="after")
    def known_defects(self) -> "GeneratorConfig":
        if not set(self.defects) <= set(DEFECTS) or len(set(self.defects)) != len(self.defects):
            raise ValueError("unknown_or_repeated_defect")
        if "D5" not in self.defects:
            raise ValueError("d5_is_the_storage_convention")  # timestamps are always stored in UTC; D5 is the mismatch with the local business day
        return self

    @property
    def n_orders(self) -> int:
        if self.tier not in TIERS:
            raise ValueError("unknown_tier")
        return TIERS[self.tier]

    def on(self, defect: str) -> bool:
        return defect in self.defects

    def digest(self) -> str:
        body = {"version": GENERATOR_VERSION, **self.model_dump(mode="json")}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
