#!/bin/sh
cd "$(dirname "$0")" || exit 1
exec ./helper.py \
  --capture-mode game-window \
  --verify-mode always \
  --verify-style independent \
  --search-timeout 2.5 \
  --search-rounds 3 \
  --search-results 5 \
  --max-context-items 12 \
  --max-total-seconds 10 \
  "$@"
