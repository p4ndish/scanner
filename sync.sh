#!/usr/bin/env bash
#
# sync.sh — pull the latest code from GitHub and reload the running stack.
#
# Use this on any instance that's already set up (via start.sh) to bring in
# new changes. It:
#   1. git pull
#   2. rebuilds the frontend (uses npm if present, else a node docker container)
#   3. reloads the web + worker processes so bind-mounted Python code takes effect
#
# Usage:
#   ./sync.sh              # pull + build + reload
#   ./sync.sh --no-build   # skip frontend rebuild (code-only change)
#   ./sync.sh --check      # show what would update (git fetch + status), no changes
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -t 1 ]; then
  GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; BLU=$'\033[0;34m'; BLD=$'\033[1m'; RST=$'\033[0m'
else
  GRN=""; YLW=""; BLU=""; BLD=""; RST=""
fi
log()  { echo "${BLU}==>${RST} ${BLD}$*${RST}"; }
ok()   { echo "${GRN}  ✓${RST} $*"; }
die()  { echo "  ✗ $*" >&2; exit 1; }

NO_BUILD=false
CHECK=false
case "${1:-}" in
  --no-build) NO_BUILD=true ;;
  --check)    CHECK=true ;;
esac

# ── 0. sanity ──
command -v git    >/dev/null 2>&1 || die "git not found"
command -v docker >/dev/null 2>&1 || die "docker not found"
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"
else die "docker compose not available"; fi

echo "${BLD}${BLU}  sync — $(basename "$SCRIPT_DIR")${RST}"

# ── 1. check what's pending (or pull) ──
if [ "$CHECK" = true ]; then
  log "Checking for updates (no changes made)"
  git fetch origin
  AHEAD=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
  if [ "$AHEAD" = "0" ]; then ok "already up to date"; else
    echo "  ${YLW}$AHEAD commit(s) behind origin/main:${RST}"
    git log --oneline HEAD..origin/main | sed 's/^/    /'
  fi
  exit 0
fi

log "Pulling latest code"
git pull --ff-only origin main || die "git pull failed (resolve local changes / merge conflicts, then re-run)"
ok "code up to date: $(git log --oneline -1)"

# ── 2. rebuild frontend ──
if [ "$NO_BUILD" = false ]; then
  log "Building frontend"
  if command -v npm >/dev/null 2>&1; then
    ( cd frontend && npm install --no-audit --no-fund --silent && npm run build --silent )
    ok "frontend built (via npm)"
  else
    # npm missing on the host → build inside a node container
    echo "  npm not found on host — building in node:20-alpine container"
    docker run --rm -v "$PWD/frontend":/app -w /app node:20-alpine \
      sh -c 'npm install --no-audit --no-fund && npm run build' \
      || die "frontend build failed"
    ok "frontend built (via docker node container)"
  fi
else
  ok "skipping frontend rebuild (--no-build)"
fi

# ── 3. reload the stack ──
# `up -d` creates any missing containers; `restart` reloads the bind-mounted
# Python code in the web/worker processes (up -d alone won't reload code that's
# bind-mounted, since the image hasn't changed).
log "Reloading stack"
$COMPOSE up -d
$COMPOSE restart web worker nginx 2>/dev/null || $COMPOSE restart web worker
ok "stack reloaded"

# ── 4. quick health check ──
WEB_PORT="$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 || echo 12211)"
WEB_PORT="${WEB_PORT:-12211}"
log "Health check"
# uvicorn needs a moment after restart before it answers
HEALTHY=false
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://localhost:${WEB_PORT}/api/health" >/dev/null 2>&1; then HEALTHY=true; break; fi
  sleep 2
done
if [ "$HEALTHY" = true ]; then
  ok "${GRN}synced & healthy${RST} → http://localhost:${WEB_PORT}"
else
  echo "  ${YLW}API not responding yet — it may still be starting. Check:${RST} docker compose logs --tail=20 web"
fi
