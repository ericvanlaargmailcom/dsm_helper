#!/bin/sh
cd "$(dirname "$0")" || exit 1
export LLM_PROVIDER="openai-responses"
export LLM_ENDPOINT="https://api.openai.com/v1/responses"
export LLM_MODEL="gpt-5.5"
exec ./start_auto.sh \
  --trigger-mode paste \
  --reasoning-effort none \
  --max-tokens 128 \
  --timeout 10 \
  --search-timeout 1 \
  --search-rounds 1 \
  --search-results 4 \
  --max-context-items 4 \
  --max-total-seconds 6 \
  --verify-mode smart \
  --no-tiebreak-search \
  --no-evidence-check \
  "$@"
