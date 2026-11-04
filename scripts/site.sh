#!/usr/bin/env bash
# Shared loader for script entry points. Values in the calling environment take precedence.
# local/site.env must contain literal KEY=VALUE assignments (quote spaces and regexes).
SITE_REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
site_load() {
  local config="$SITE_REPO/local/site.env" line key had_allexport=0
  local -A overrides=()
  [[ -f "$config" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
      key=${BASH_REMATCH[1]}
      if [[ -v "$key" ]]; then overrides["$key"]=${!key}; fi
    fi
  done < "$config"
  [[ $- != *a* ]] || had_allexport=1
  set -a
  source "$config"
  if [[ "$had_allexport" == 0 ]]; then set +a; fi
  for key in "${!overrides[@]}"; do
    printf -v "$key" '%s' "${overrides[$key]}"
    export "$key"
  done
}
site_require() {
  local key
  for key in "$@"; do
    if [[ -z "${!key:-}" ]]; then
      echo "Missing site setting $key; set it in local/site.env or the environment (see config/site.example.env)" >&2
      return 2
    fi
  done
}
site_load
