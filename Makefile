PYTHON ?= python3

.PHONY: all test trace quality docs dashboard dashboard-reviewer dashboard-proxied auth-setup auth-up auth-down proxy-walkthrough a11y-audit export-summary browser-walkthrough tableau-extract copilot-eval edge-eval audit-edge-eval verify-dbt weekly-check launchd-template

all:
	$(PYTHON) pipeline.py all

test:
	$(PYTHON) -m unittest discover -v

trace:
	$(PYTHON) pipeline.py trace --figure-id REC-PAY-00001

quality:
	$(PYTHON) pipeline.py quality

docs:
	$(PYTHON) data_dictionary.py

dashboard:
	$(PYTHON) -m streamlit run dashboard.py

dashboard-reviewer:
	FINRECON_ROLE=reviewer $(PYTHON) -m streamlit run dashboard.py

AUTH_COMPOSE = docker compose --env-file ops/auth/local/.env -f ops/auth/compose.yaml

# Local login: write secrets and demo users (git-ignored), start Caddy +
# oauth2-proxy + Dex on 127.0.0.1:8088, then run the app in proxy mode.
auth-setup:
	$(PYTHON) ops/auth/setup_local.py

auth-up:
	$(AUTH_COMPOSE) up -d

auth-down:
	$(AUTH_COMPOSE) down

dashboard-proxied:
	set -a; . ops/auth/local/app.env; set +a; \
	$(PYTHON) -m streamlit run dashboard.py --server.address 127.0.0.1 \
		--server.port $$FINRECON_APP_PORT --server.headless true

proxy-walkthrough:
	$(PYTHON) -m eval.proxy_walkthrough

a11y-audit:
	$(PYTHON) -m eval.a11y_audit --label after

export-summary:
	$(PYTHON) pipeline.py export-summary --out data/summary_export

browser-walkthrough:
	$(PYTHON) -m eval.browser_walkthrough

tableau-extract:
	$(PYTHON) tableau_export.py --audience summary

copilot-eval:
	$(PYTHON) -m eval.copilot_eval

edge-eval:
	$(PYTHON) -m eval.independent_cases

audit-edge-eval:
	$(PYTHON) -m eval.independent_audit_cases

verify-dbt:
	$(PYTHON) verify_dbt_models.py

weekly-check:
	$(PYTHON) scheduler.py run-if-due

launchd-template:
	$(PYTHON) scheduler.py render-launchd
