#!/bin/sh
# Injects runtime configuration into the built SPA before starting Gunicorn.
# Placeholders live in web-vue/index.html and survive the Vite build verbatim.
set -eu

INDEX="/app/static/dist/index.html"
GA_ID="${GOOGLE_ANALYTICS_ID:-}"
SITE_VERIFICATION="${GOOGLE_SITE_VERIFICATION:-}"
BA_ID="${BAIDU_ANALYTICS_ID:-}"

if [ -f "$INDEX" ]; then
  if [ -n "$GA_ID" ]; then
    sed -i "s/__GA_ID__/$GA_ID/g" "$INDEX"
  else
    sed -i 's/__GA_ID__//g' "$INDEX"
  fi

  if [ -n "$BA_ID" ]; then
    sed -i "s/__BA_ID__/$BA_ID/g" "$INDEX"
  else
    sed -i 's/__BA_ID__//g' "$INDEX"
  fi

  if [ -n "$SITE_VERIFICATION" ]; then
    sed -i "s/__GOOGLE_SITE_VERIFICATION__/$SITE_VERIFICATION/g" "$INDEX"
  else
    # Drop the whole meta tag so no placeholder leaks to crawlers.
    sed -i '/data-runtime-config/d' "$INDEX"
  fi
fi

exec "$@"
