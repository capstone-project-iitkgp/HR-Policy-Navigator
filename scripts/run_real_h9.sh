#!/usr/bin/env bash
# Real-endpoint H9 run (27 Nov). Run from Code/ in your own terminal, Zscaler OFF:
#   bash scripts/run_real_h9.sh [probe|build|main]      (default: all three in order)
# Keys are typed at a hidden prompt and live only in this process. Nothing is written to disk.
set -euo pipefail
cd "$(dirname "$0")/.."
STEP="${1:-all}"
ROOT_CERTS="$(cd .. && pwd)"

read -rsp "Chat API key (port 1033): " API_KEY; echo
read -rsp "Embeddings key (port 1035; Enter = same as chat key): " EMBED_API_KEY; echo
[ -z "$EMBED_API_KEY" ] && EMBED_API_KEY="$API_KEY"
export API_KEY EMBED_API_KEY
export CHAT_BASE_URL="https://103.42.50.41:1033/v1"
export CHAT_MODEL="Qwen/Qwen3-8B"
export CAPSTONE_CA_CERT="$ROOT_CERTS/02_Team_HR_Policy_Navigator/certs/vm05-ca.crt"
export EMBED_BASE_URL="https://103.42.50.41:1035/v1"
export EMBED_MODEL="bge-small-en-v1.5"
export EMBED_CA_CERT="$ROOT_CERTS/Embedding_EndPoint/VM02_Embeddings_Access/vm02-ca.crt"
export PYTHONPATH="src:."
PY=.venv/bin/python

if [ "$STEP" = probe ] || [ "$STEP" = all ]; then
  echo "== 1/3 endpoint probe =="; $PY scripts/probe_endpoint.py
fi
if [ "$STEP" = build ] || [ "$STEP" = all ]; then
  echo "== 2/3 dense index (embeds every chunk once) =="; $PY -m hr_navigator.retrieval.build --dense
fi
if [ "$STEP" = attacks ]; then
  echo "== security tests through /chat (about 5 min; do not run during H9) =="
  cp -n security_tests/results.csv security_tests/results_20nov.csv || true
  $PY -m security_tests.run_attacks 2>&1 | tee evaluation/results/attacks_27nov.log
fi
if [ "$STEP" = main ] || [ "$STEP" = all ]; then
  echo "== 3/3 H9 main, 1 repeat (about 35 min; resumable) =="
  $PY -m evaluation.h9_run --stage main --repeats 1 --variants v2_rag,v3_agent 2>&1 | tee -a evaluation/results/h9_run_27nov.log
fi
