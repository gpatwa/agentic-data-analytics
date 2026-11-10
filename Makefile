# Makefile

.PHONY: help install-analytics analytics-reference-smoke analytics-reference-up analytics-golden analytics-triage-eval analytics-proposals-eval analytics-dbt-eval analytics-prompts-eval analytics-review-eval analytics-evaluation-eval analytics-rollout-eval analytics-drift-eval analytics-poisoning-eval analytics-security-eval analytics-resilience-drill dev-analytics-api dev-analytics-web dev-products up down infra test-analytics infra-staging bootstrap-github-repo deploy-analytics-azure deploy-landing-azure observability-up observability-down lint format architecture-check architecture-build dev-split

help:
	@echo "RAG Platform Commands:"
	@echo ""
	@echo "  Local Development:"
	@echo "    make install       - Install Python dependencies"
	@echo "    make up            - Start local DBs (Docker)"
	@echo "    make init          - Initialize local DBs, collections, indexes"
	@echo "    make dev           - Run FastAPI server locally (hot reload)"
	@echo "    make dev-support-web - Run the support web product on port 5173"
	@echo "    make dev-analytics-api - Run the analytics API on port 8090"
	@echo "    make dev-analytics-web - Run the analytics web product on port 5174"
	@echo "    make analytics-reference-smoke - Run both governed smoke journeys (fakes only)"
	@echo "    make analytics-reference-up - Serve the governed v2 API locally with fakes (port 8095)"
	@echo "    make analytics-golden - Run the M4 golden suite and write its report (fakes only)"
	@echo "    make analytics-triage-eval - Run the ADS-051 triage corpus and write its report (fakes only)"
	@echo "    make analytics-proposals-eval - Run the ADS-052 proposal corpus and write its report (fakes only)"
	@echo "    make analytics-dbt-eval - Run the ADS-053 dbt proposal corpus and write its report (fakes only)"
	@echo "    make analytics-prompts-eval - Run the ADS-054 prompt registry corpus and write its report (fakes only)"
	@echo "    make analytics-review-eval - Run the ADS-055 review workflow corpus and write its report (fakes only)"
	@echo "    make analytics-evaluation-eval - Run the ADS-056 candidate evaluation corpus and write its report (fakes only)"
	@echo "    make analytics-rollout-eval - Run the ADS-057 rollout corpus and write its report (fakes only)"
	@echo "    make analytics-drift-eval - Run the ADS-058 drift corpus and write its report (synthetic windows)"
	@echo "    make analytics-poisoning-eval - Run the ADS-059 poisoning corpus and write its report (fakes only)"
	@echo "    make analytics-security-eval - Run the ADS-067 security corpus and findings register and write its report (fakes only)"
	@echo "    make analytics-resilience-drill - Run the ADS-068 restore and failover drills and write their report (local, fakes only)"
	@echo "    make dev-products  - Run both products as Docker deployables"
	@echo "    make observability-up - Start Grafana, Prometheus, exporters, and admin UIs"
	@echo "    make observability-down - Stop the local operations plane"
	@echo "    make support-demo  - Validate Resolution Intelligence demo workflow"
	@echo "    make demo-ready-local - Start local deps, seed demo data, and run demo acceptance"
	@echo "    make ingest FILE=x - Ingest a file, directory, or --sample"
	@echo "    make down          - Stop local DBs (Docker only)"
	@echo "    make stop          - Stop everything (API + Docker + stale processes)"
	@echo "    make test          - Run tests"
	@echo ""
	@echo "  AWS Deployment:"
	@echo "    make deploy-aws       - Full deploy: Terraform + Build + Bootstrap (first time, prod)"
	@echo "    make infra            - Apply Terraform (prod)"
	@echo "    make infra-staging    - Apply Terraform (staging)"
	@echo "    make build            - Build & push Docker image to ECR"
	@echo "    make bootstrap        - Bootstrap EKS cluster (prod)"
	@echo "    make bootstrap-staging- Bootstrap EKS cluster (staging)"
	@echo "    make init-cloud       - Initialize cloud databases (prod)"
	@echo "    make deploy           - Helm upgrade API only (prod, code changes)"
	@echo "    make deploy-staging   - Helm upgrade API only (staging, code changes)"
	@echo "    make verify           - Full post-deploy verify (pods + DB init + tests + UI)"
	@echo "    make smoke-test       - Run smoke tests only"
	@echo "    make destroy          - Tear down ALL AWS resources (prod)"
	@echo ""
	@echo "  Azure Deployment:"
	@echo "    make deploy-azure      - Full deploy: Terraform + Build + Bootstrap (first time)"
	@echo "    make infra-azure       - Apply Azure Terraform only"
	@echo "    make build-azure       - Build & push Docker image to ACR"
	@echo "    make bootstrap-azure   - Bootstrap AKS cluster (K8s resources)"
	@echo "    make deploy-api-azure  - Helm upgrade API only (code changes)"
	@echo "    make deploy-analytics-azure - Build and deploy analytics API + web"
	@echo "    make deploy-landing-azure - Build and publish public landing to Azure Static Web Apps"
	@echo "    make pause-azure       - Pause billing: stop Postgres, delete Redis, release IPs, prune ACR, stop App Service, trim Log Analytics (~\$$19/day saved)"
	@echo "    make resume-azure      - Resume: start Postgres + App Service back up (Redis recreated on next deploy)"
	@echo "    make import-azure      - Import manually-created resources into Terraform state (run once)"
	@echo "    make destroy-azure     - Tear down ALL Azure resources + verify"
	@echo "    make discovery-repo-vars-plan - Show the GitHub repo variables the Discovery workflows need (from terraform output)"
	@echo "    make discovery-repo-vars      - Set them (idempotent; uses your gh session)"
	@echo "    make azure-dev-up      - Provision/update remote Docker development VM and start Compose"
	@echo "    make azure-dev-sync    - Sync this checkout to the remote Docker VM"
	@echo "    make azure-dev-start   - Start the VM and remote Compose services"
	@echo "    make azure-dev-stop    - Deallocate the VM while preserving Docker volumes"
	@echo "    make azure-dev-status  - Show remote Docker VM status"
	@echo "    make azure-dev-ssh     - Open an SSH shell on the remote Docker VM"
	@echo "    make azure-dev-tunnel  - Forward local app/API/search ports over SSH"
	@echo "    make azure-dev-destroy - Destroy the isolated remote-dev environment"
	@echo ""
	@echo "  Developer Setup:"
	@echo "    make setup         - Install deps + pre-commit hooks"
	@echo "    make lint          - Run ruff linter"
	@echo "    make format        - Auto-fix lint + format code"
	@echo "    make architecture-check - Validate Archify diagram sources at showcase quality"
	@echo "    make architecture-build - Validate and regenerate interactive architecture HTML"
	@echo ""
	@echo "  Split-Plane Development:"
	@echo "    make dev-control-plane  - Run control plane locally"
	@echo "    make dev-data-plane     - Run data plane locally"
	@echo "    make dev-split          - Run both planes via Docker Compose"
	@echo "    make test-control-plane - Run control plane tests"
	@echo "    make test-data-plane    - Run data plane tests"
	@echo "    make test-all           - Run all tests (monolith + split)"
	@echo ""
	@echo "  Quick start (local):  make setup && make up && make init && make dev"
	@echo "  Quick start (AWS):    make deploy-aws && make verify"
	@echo "  Quick start (Azure):  make deploy-azure"
	@echo ""
	@echo "  Post-Destroy Verification:"
	@echo "    make verify-cleanup              - Check for orphaned AWS resources"
	@echo "    make verify-cleanup-delete       - Check + delete orphaned AWS resources"
	@echo "    make verify-cleanup-azure        - Check for orphaned Azure resources"
	@echo "    make verify-cleanup-azure-delete - Check + delete orphaned Azure resources"
	@echo ""
	@echo "  Quick start (split):  make up && make dev-split"

# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

install-analytics:
	pip install -r services/analytics-api/requirements.txt

lint:
	ruff check

format:
	ruff check --fix && ruff format

architecture-check:
	./scripts/architecture/archify.sh validate architecture docs/diagrams/agentic-data-stack.architecture.json --quality showcase --json
	./scripts/architecture/archify.sh validate workflow docs/diagrams/agentic-data-stack-request.workflow.json --quality showcase --json

architecture-build: architecture-check
	./scripts/architecture/archify.sh deliver architecture docs/diagrams/agentic-data-stack.architecture.json docs/diagrams/agentic-data-stack-system.html --quality showcase --json
	./scripts/architecture/archify.sh deliver workflow docs/diagrams/agentic-data-stack-request.workflow.json docs/diagrams/agentic-data-stack-request.html --quality showcase --json

up:
	docker compose up -d

observability-up:
	docker compose --profile observability up -d

observability-down:
	docker compose --profile observability down

down:
	docker compose down

dev-analytics-api:
	cd services/analytics-api && PYTHONPATH=.:../.. uvicorn app.main:app --reload --host 0.0.0.0 --port 8090

dev-analytics-web:
	cd apps/analytics-web && npm run dev -- --host 0.0.0.0

dev-products:
	docker compose --profile products up --build

# Local reference stack (fakes only, no network): both smoke journeys, or serve the v2 API locally.
analytics-reference-smoke:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack smoke

analytics-reference-up:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack up

# ADS-051 triage corpus (fakes only), reported against the user-approved triage thresholds.
analytics-triage-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack triage --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-052 change-proposal corpus (fakes only), reported against the user-approved thresholds.
analytics-proposals-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack proposals --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-053 dbt proposal corpus (fakes only; dbt is never run), reported against the user-approved thresholds.
analytics-dbt-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack dbt --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-054 prompt/example registry corpus (fakes only); thresholds are proposed until the user approves them.
analytics-prompts-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack prompts --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-055 review workflow corpus (fakes only); thresholds are proposed until the user approves them.
analytics-review-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack review --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-056 candidate evaluation corpus (fakes only); thresholds are proposed until the user approves them.
analytics-evaluation-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack evaluation --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-057 rollout corpus (fakes only; simulated observers); thresholds are proposed until the user approves them.
analytics-rollout-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack rollout --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-058 drift corpus (synthetic windows); alert rules and thresholds are proposed until the user approves them.
analytics-drift-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack drift --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-059 closed-loop safety and poisoning corpus (fakes only); thresholds are proposed until the user approves them.
analytics-poisoning-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack poisoning --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-067 security corpus and findings register (fakes only); thresholds are proposed until the user approves them.
analytics-security-eval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack security --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-068 restore and failover drills (local SQLite/DuckDB, fakes); objectives are proposed until the user approves them.
analytics-resilience-drill:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack resilience --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-069 seeded-failure operations drill (fakes only): alerts, runbooks, diagnosis, and replay.
analytics-operations-drill:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack operations --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-071 end-to-end correctness and PostgreSQL/DuckDB equivalence on the ADS-070 corpus (fakes only); thresholds and corpus are proposed.
analytics-m7-correctness:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 071 --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-072 context/ontology retrieval, provenance, freshness, and token-efficiency evaluation (fakes only); thresholds are proposed.
analytics-m7-retrieval:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 072 --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-073 ambiguity, abstention, unsupported-question, and calibration evaluation (fakes only); thresholds are proposed.
analytics-m7-abstention:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 073 --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-074 security, tenant-isolation, and policy adversarial evaluation (fakes only); thresholds are proposed. Not a security review.
analytics-m7-isolation:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 074 --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-075 reliability, crash/replay, dependency-failure, and chaos evaluation (fakes only); thresholds are proposed.
analytics-m7-reliability:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 075 --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-076 latency, concurrency, token, warehouse, and cost evaluation (fakes only); thresholds and prices are proposed placeholders.
analytics-m7-performance:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 076 --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-077 provider variance, degraded-mode, and rollback evaluation (fakes only); thresholds are proposed.
analytics-m7-provider:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 077 --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-078 second-tenant and Apache Ossie onboarding drill (fakes only; sandbox registry, no certification); thresholds are proposed.
analytics-m7-onboarding:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 078 --write-report ../../docs/execution/enterprise-analytics/reports

# ADS-079 final evidence manifest: links artifacts by digest and lists what blocks a release. It never decides or signs; regenerate after any change.
analytics-m7-manifest:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack m7 079 --write-report ../../docs/execution/enterprise-analytics/reports

# M7-H4 cost-unit calibration mechanism checks (fakes only; not a warehouse calibration; nothing is applied by default).
analytics-cost-calibration:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack calibration --write-report ../../docs/execution/enterprise-analytics/reports

# M4 golden suite (fakes only); regenerates the report against the user-approved M4 thresholds.
analytics-golden:
	cd services/analytics-api && PYTHONPATH=.:../.. python3 -m reference_stack golden --write-report ../../docs/execution/enterprise-analytics/reports

test-analytics:
	cd services/analytics-api && PYTHONPATH=.:../.. pytest

seed-olist:
	python3 scripts/seed_olist.py

# Generic dataset loader — works with any CSV dataset
# Usage:
#   make seed-dataset NAME=olist PATH=data/olist/
#   make seed-dataset NAME=sales KAGGLE=username/sales-data
#   make seed-dataset NAME=hr PATH=data/hr/ PREFIX=hr_
seed-dataset:
	@if [ -z "$(NAME)" ]; then \
		echo "Usage:"; \
		echo "  make seed-dataset NAME=olist PATH=data/olist/"; \
		echo "  make seed-dataset NAME=sales KAGGLE=user/dataset"; \
		echo "  make seed-dataset NAME=hr PATH=data/hr/ PREFIX=hr_"; \
		exit 1; \
	fi
	@ARGS="--name $(NAME)"; \
	if [ -n "$(KAGGLE)" ]; then ARGS="$$ARGS --kaggle $(KAGGLE)"; fi; \
	if [ -n "$(PREFIX)" ]; then ARGS="$$ARGS --prefix $(PREFIX)"; fi; \
	if [ -n "$(FORCE)" ]; then ARGS="$$ARGS --force"; fi; \
	if [ -n "$(PATH)" ]; then python3 scripts/seed_dataset.py $(PATH) $$ARGS; \
	else python3 scripts/seed_dataset.py $$ARGS; fi

# ============================================================
# AWS DEPLOYMENT
# ============================================================

# Terraform only: provision VPC, EKS, Aurora, Redis, S3
infra:
	cd infra/terraform && terraform init -upgrade && terraform apply

# Initialize cloud databases (Qdrant collections, Neo4j indexes)
# Plan the repository settings stack (auto-merge, branch cleanup, CI gate ruleset). APPLY=1 applies it.
bootstrap-github-repo:
	@APPLY=$(APPLY) ./scripts/bootstrap_github_repo.sh

# Terraform for staging (isolated state key + staging.tfvars)
infra-staging:
	cd infra/terraform && \
	terraform init -upgrade -reconfigure \
	    -backend-config="key=staging/terraform.tfstate" && \
	terraform apply -var-file=envs/staging.tfvars

# Build and deploy the standalone analytics product to an existing Azure AKS cluster.
deploy-analytics-azure:
	./scripts/deploy_azure_analytics.sh

# Build and publish the public landing page to Azure Static Web Apps.
deploy-landing-azure:
	./scripts/deploy_azure_landing.sh

# Run both planes via Docker Compose (with all dependencies)
dev-split:
	docker compose --profile split up -d
