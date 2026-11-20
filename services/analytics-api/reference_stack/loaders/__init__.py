"""Loaders that put reference data into a warehouse (docs/design/G5_LOAD_APPROVAL_REQUEST.md).

They build a plan (every statement, every check) and can print it without running anything. Running it against a real warehouse is G5,
which needs the owner's explicit approval; no connector is wired here. Execution takes an injected session so the logic is tested on fakes.
"""
