"""Tooling for the product-run live end-to-end check (docs/design/CONNECT_PRODUCT_RUN_E2E_PLAN.md).

The statements, the leak scanner and a local harness that runs the real app on this machine. Nothing here connects to anything
by itself: the harness refuses to start without `--allow-live` and a matching `--expect-account`, and every live step needs
the owner's explicit approval at its gate (G1, G2, G3).
"""
