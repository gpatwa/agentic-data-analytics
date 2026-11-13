# Non-personal, non-secret inputs of the live pilot. Personal inputs (budget_emails) live in private storage and are
# fetched by the workflow; secrets are read from the Key Vault at run time (see ci-prepare.sh).
environment           = "pilot"
location              = "eastus"
create_openai         = true
create_entra_app      = true
min_cohort            = "10"
monthly_budget_usd    = 100
enable_public_demo    = true
api_openai_deployment = "gpt-6-luna"
