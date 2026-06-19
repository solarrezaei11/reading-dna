#!/bin/bash
set -e
cd "$(dirname "$0")"
source venv/bin/activate
export $(grep -v '^#' ../.env.local | xargs)
uvicorn main:app --reload --port 8000
