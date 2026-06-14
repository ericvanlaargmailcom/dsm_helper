#!/bin/sh
cd "$(dirname "$0")" || exit 1
exec ./helper.py \
  --capture-mode game-window \
  --verify-mode always \
  --verify-style independent \
  --search-timeout 4 \
  --search-rounds 4 \
  --search-results 6 \
  --max-context-items 18 \
  --max-total-seconds 14 \
  "$@"
