#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "init: $*" >&2; exit 1; }

py=""
for candidate in ${PYTHON:-python3 python}; do
    if "$candidate" -c "" >/dev/null 2>&1; then
        py="$candidate"
        break
    fi
done
[ -n "$py" ] || fail "Python not found; install Python 3.11+ or set PYTHON"

"$py" -c "import sys; sys.exit(sys.version_info < (3, 11))" \
    || fail "Python 3.11+ required, found $("$py" -V 2>&1)"

if [ ! -d .venv ]; then
    echo "Creating .venv with $("$py" -V 2>&1)"
    "$py" -m venv .venv
fi

if [ -x .venv/bin/python ]; then
    venv_py=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then
    venv_py=.venv/Scripts/python.exe
else
    fail ".venv has no Python interpreter; delete .venv and run again"
fi

"$venv_py" -m pip install --upgrade pip
"$venv_py" -m pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env; set GITHUB_TOKEN in it before crawling."
fi

cat <<EOF

Setup complete. Next:
  $venv_py -m crawler check
  $venv_py -m crawler fill        (or: run --hours 2)
  $venv_py -m sbs corpus all
  $venv_py -m pytest
EOF
