#!/usr/bin/env bash
# Apply SQL migrations by edition. Usage: ./scripts/apply_migrations.sh community|cloud|learning|all-community
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIG="$ROOT/db/migrations"
EDITION="${1:-community}"
URL="${DB_URL:-${DATABASE_URL:-}}"
if [[ -z "$URL" ]]; then
  echo "Set DB_URL or DATABASE_URL" >&2
  exit 1
fi

apply_file() {
  local f="$1"
  echo "→ $f"
  psql "$URL" -v ON_ERROR_STOP=1 -f "$f"
}

case "$EDITION" in
  community|all-community)
    # All root SQL except learning (optional)
    while IFS= read -r -d '' f; do
      base="$(basename "$f")"
      if [[ "$base" == "add_pipeline_learning.sql" || "$base" == "add_learning_retrieval_log.sql" ]]; then
        continue
      fi
      apply_file "$f"
    done < <(find "$MIG" -maxdepth 1 -name '*.sql' -print0 | sort -z)
    if [[ "$EDITION" == "all-community" ]]; then
      apply_file "$MIG/add_pipeline_learning.sql"
      apply_file "$MIG/add_learning_retrieval_log.sql"
    fi
    ;;
  learning)
    apply_file "$MIG/add_pipeline_learning.sql"
    apply_file "$MIG/add_learning_retrieval_log.sql"
    ;;
  cloud)
    while IFS= read -r -d '' f; do
      apply_file "$f"
    done < <(find "$MIG/cloud" -maxdepth 1 -name '*.sql' -print0 | sort -z)
    ;;
  *)
    echo "Usage: $0 community|all-community|learning|cloud" >&2
    exit 1
    ;;
esac
echo "Done ($EDITION)."
