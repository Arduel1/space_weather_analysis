#!/bin/sh
# Runs every check of the project from its root and stops at the first failing stage.
# Usage: sh scripts/gate.sh [--without-integration]
set -eu

# Marker expression of pytest; empty runs every test, including the integration tests that start MongoDB via Docker
test_markers=""
if [ "${1:-}" = "--without-integration" ]; then
    test_markers="not integration"
fi

# Runs one stage and ends the script with the name of the stage if it fails.
stage() {
    name=$1
    shift
    echo "== $name"
    if ! "$@"; then
        echo "gate.sh: stage '$name' failed" >&2
        exit 1
    fi
}

# pip-audit reads the locked versions from a requirements file; --no-deps, as the file already lists every package
audit_locked_packages() {
    requirements=$(mktemp)
    trap 'rm -f "$requirements"' EXIT
    uv export --frozen --all-groups --no-hashes --no-emit-project --quiet --output-file "$requirements"
    pip-audit --requirement "$requirements" --no-deps --disable-pip --progress-spinner off
}

# Every module of the web frontend
check_frontend_syntax() {
    for module in web/js/*.js; do
        node --check "$module" || return 1
    done
}

stage "ruff" ruff check src tests
stage "ruff format" ruff format --check src tests
stage "mypy" mypy
# No pytest cache, as the QA container mounts the project read-only
stage "pytest" python -m pytest -q -p no:cacheprovider -m "$test_markers"
stage "pip-audit" audit_locked_packages
stage "deptry" deptry src
stage "node --check" check_frontend_syntax
echo "gate.sh: every stage passed"
