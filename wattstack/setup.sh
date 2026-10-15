#!/usr/bin/env bash
# One-command setup, mirroring glasshouse's setup.sh: creates both
# packages' virtual environments, installs everything (web installs
# core as an editable local dependency), runs migrations, and runs
# every test suite. Safe to re-run.
set -euo pipefail

command -v python3 >/dev/null || { echo "python3 not found -- install Python 3.11+"; exit 1; }

echo "== core: venv + install =="
cd core
python3 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[dev]"
echo "== core: tests =="
pytest -q
deactivate
cd ..

echo "== web: venv + install =="
cd web
python3 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ../core
pip install -q -e ".[dev]"
echo "== web: migrate =="
python manage.py migrate --no-input -v 0
echo "== web: tests =="
python manage.py test scenarios
deactivate
cd ..

echo "== ingestion: venv + install =="
cd ingestion
python3 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[dev]"
echo "== ingestion: tests =="
pytest -q
deactivate
cd ..

echo ""
echo "All good. To run things:"
echo "  CLI:      cd core && . .venv/bin/activate && wattstack run --config scenarios/example.yaml"
echo "  Web UI:   cd web  && . .venv/bin/activate && python manage.py runserver"
echo "  Explore:  cd ingestion && . .venv/bin/activate && wattstack-explore --verify-only"
