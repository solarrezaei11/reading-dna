#!/bin/bash
set -e
cd "$(dirname "$0")"
source venv/bin/activate

if [[ -f ../.env.local ]]; then
  line_number=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line_number=$((line_number + 1))
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue

    if [[ "$line" != *=* ]]; then
      echo "Invalid environment entry at .env.local line $line_number." >&2
      exit 1
    fi

    name="${line%%=*}"
    value="${line#*=}"
    name="${name#"${name%%[![:space:]]*}"}"
    name="${name%"${name##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"

    if [[ ! "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "Invalid environment variable name at .env.local line $line_number." >&2
      exit 1
    fi

    if [[ ${#value} -ge 2 ]] && {
      [[ "$value" == \"*\" ]] || [[ "$value" == \'*\' ]];
    }; then
      value="${value:1:${#value}-2}"
    fi

    export "$name=$value"
  done < ../.env.local
fi

python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
