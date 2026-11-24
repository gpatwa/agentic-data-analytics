# Try the Databricks screens against a real workspace, locally (no deployment)

`python -m reference_stack.local_stack --web` (from `services/analytics-api`, with `PYTHONPATH=.:../..`) runs the real app and the web dev server on this computer. It is for looking at and clicking through the Databricks Connect experience end to end. It makes **no call to any workspace by itself**: warehouse calls happen only when you run the generated enrol or rotate file in your own terminal under your own Databricks profile, or switch warm-up on in the screen.

## What runs for real, and what does not

| Real here | Needs a deployed instance to check |
|---|---|
| The routes, the Connect wiring, the enrol and rotate endpoints, the drift monitor, the web screens | Entra sign-in (here: a test identity from a throwaway local key) |
| The Databricks connector (OAuth M2M, as the product's gateway uses it) and your workspace | The Key Vault secret store (here: in memory, gone when you stop it) |
| The generated files, run by you under your profile | A public https address and TLS (here: `http://127.0.0.1`) |
| The warm-up path (switch it on in the screen) | Other people's browsers and networks; a restart that keeps state |

## Steps

1. From `services/analytics-api`: `PYTHONPATH=.:../.. python3 -m reference_stack.local_stack --web`. It prints the addresses and writes a test token to `.local-stack/token.txt` (git-ignored, owner-only; it lasts about an hour).
2. Open http://127.0.0.1:5174, choose **Connect a warehouse**, then **Databricks (internal testing)**, paste the token, choose **Open**.
3. Enter the workspace host, the catalog, the tables as `SCHEMA.TABLE`, and the SQL warehouse's name (for the owner's test workspace: the certified synthetic views `dev.compass_synthetic_food_delivery.orders_current` and `.orders_usd_gross`, warehouse "Serverless Starter Warehouse"). Choose **Show the script**.
4. Keep **One file (recommended)**, choose **Get the file**, download it, and in your own terminal run `python3 compass-enrol.py --profile <your databricks profile>` as the screen says. The screen goes from waiting to connected, with each table read.
5. Try **Check now**, the warm-up switch, **Rotate with a file** (download and run `compass-rotate.py` the same way), and **Remove**; then run `python3 compass-enrol.py --remove <application id> --profile <profile>` as the screen shows, so nothing stays in the workspace.
6. Ctrl-C stops everything and deletes the token file.

A question asked in the Governed tab will not be answered: no model is configured here by design.
