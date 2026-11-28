Recorded inputs for the DuckDB end-to-end runs (`reference_stack/e2e_duckdb.py`, `e2e_run.py`, `e2e_local.py`).

- `*_replies.json`: what a fresh Claude agent, standing in for the language model, replied to the exact prompt the product builds for each question (a typed intent, or `{"unsupported_request": true}`). It saw no answers. Query and tenant ids inside are overwritten by the harness.
- `*_synonyms.json`: synonyms the same kind of agent proposed from the contract text alone, never from the questions. A proposal, not a certification; the validator keeps only what it accepts.
- Olist files are for internal use only (its licence is reported as non-commercial).

Regenerate by capturing prompts (`e2e_run <dataset> ... prompts OUT.json`), giving each prompt to an agent, and saving its replies.
