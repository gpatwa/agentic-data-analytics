# Switching on K3 and the certified contracts: the exact settings, prepared not applied (2026-11-21)

Status: **prepared for the owner; nothing is deployed or changed** (program file `k3_v2_settings_prepare_only`). No environment is named yet; the sandbox tenant has no deployment target in this repository. S6 stays off in every case below.

## Two things to switch on, and why together

1. **K3, the coverage check:** `ANALYTICS_INTENT_COVERAGE_TENANTS=<tenant>` (a comma list, or `*`). It refuses a question whose words the interpreted request does not account for.
2. **A certified contract that has synonyms:** the sandbox v2 (2026-11-21) has them. The runtime refuses a contract with synonyms for a tenant without K3 (`synonyms_need_coverage`), so v2 without K3 fails closed. Turn them on in the same change.

The two synthetic-data contracts (`food-delivery-orders-current` counts and `food-delivery-orders-usd-gross` amounts) have **no** synonyms, so they do not need K3 to be served, but K3 is still the safer setting.

## The registry layout the Connect resolver reads

`ANALYTICS_CONNECT_REGISTRY_ROOT=<root>`; the resolver reads `<root>/<tenant_id>/` and requires **exactly one certified contract in that folder** (two give `contract_ambiguous`). So, per tenant:

| Tenant | Folder to create | The one contract file in it |
|---|---|---|
| `sandbox` (activate v2) | `<root>/sandbox/contracts/` | `sandbox-databricks-v2.json` from `services/analytics-api/semantic_registry_sandbox_v2/contracts/` (replacing v1 in that folder) |
| `synthetic` (both views) | `<root>/synthetic/contracts/` | `food-delivery-synthetic-views-v1.json` from `semantic_registry_synthetic_combined/contracts/` (certified 2026-11-21; the owner chose option (a) below) |

## A decision this exposed (decided 2026-11-21: option (a))

A tenant serves **one** certified contract, and the synthetic data has two (counts over `orders_current`, amounts over `orders_usd_gross`). Options: (a) certify a **combined** contract with both datasets (a new certification, one folder, one tenant); (b) use two tenants, each with its own connection and grant (more to run); (c) serve only one at a time. **Decided: (a).** The combined contract `food-delivery-synthetic-views` was certified from the two certified contracts with no new definition (program file `synthetic_views_combined_contract`). Only ids and display names changed, to stay valid in one contract: the USD dataset's `order_count` is `usd_order_count`, its dimensions are prefixed `usd_`, and the dimensions and the two counts carry no display name (the term rule forbids a name that contains another).

## Settings to apply, for the environment you name

```text
ANALYTICS_INTENT_COVERAGE_TENANTS=sandbox
ANALYTICS_CONNECT_REGISTRY_ROOT=<root containing sandbox/contracts/sandbox-databricks-v2.json>
# unchanged and NOT to be set: ANALYTICS_INTERPRETATION_CONFIRM_TENANTS stays empty (S6 off)
```

## Verify after applying (read-only)

1. The tenant's questions answer on the certified wording, and "how many orders were refunded" is **refused** (K3 closes the dropped-constraint hole).
2. A refused question's response names the requester's own unaccounted words and nothing else.
3. The tenant's runs record evidence with no question words.

## Rollback

Unset `ANALYTICS_INTENT_COVERAGE_TENANTS` **and** restore v1 in the tenant folder, together: v2 without K3 refuses every run for that tenant, so a half rollback takes the tenant down. v1 stays certified and unchanged.

## What this does not do

It deploys nothing, names no environment, and enables no S6. Applying it to an environment is a deployment step you ask for, with the environment named.
