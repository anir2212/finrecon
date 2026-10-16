PYTHON ?= python3

.PHONY: all test trace quality docs dashboard copilot-eval edge-eval audit-edge-eval verify-dbt weekly-check launchd-template

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
