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
warn() { echo "${YLW}  !${RST} $*"; }
die()  { echo "  ✗ $*" >&2; exit 1; }

# sudo helper (no-op if root)
SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"

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
BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"
git pull --ff-only origin main || die "git pull failed (resolve local changes / merge conflicts, then re-run)"
AFTER="$(git rev-parse HEAD)"
ok "code up to date: $(git log --oneline -1)"

# Did Python deps or the Dockerfile change? If so we must rebuild the image.
NEEDS_IMAGE_BUILD=false
if [ "$BEFORE" != "$AFTER" ] && [ "$BEFORE" != "none" ]; then
  if git diff --name-only "$BEFORE" "$AFTER" | grep -qE '^(requirements\.txt|Dockerfile)$'; then
    NEEDS_IMAGE_BUILD=true
    warn "requirements.txt/Dockerfile changed — will rebuild the Docker image"
  fi
fi

# ── 2. rebuild frontend ──
if [ "$NO_BUILD" = false ]; then
  log "Building frontend"
  # Old dist may be owned by root (from a past docker-container build). Make
  # sure we can overwrite it, using sudo only if a plain rm fails.
  if [ -d frontend/dist ] && ! rm -rf frontend/dist 2>/dev/null; then
    warn "dist not writable — clearing with sudo"
    ${SUDO} rm -rf frontend/dist || die "could not clear frontend/dist"
  fi
  if command -v npm >/dev/null 2>&1; then
    ( cd frontend && npm install --no-audit --no-fund --silent && npm run build --silent )
    ok "frontend built (via npm)"
  else
    # npm missing on the host → build inside a node container, AS THE HOST USER
    # (-u/-e HOME) so the output files are owned by us, not root. This prevents
    # the EACCES you get when a later host build tries to overwrite root files.
    echo "  npm not found on host — building in node:20-alpine container (as $(id -u):$(id -g))"
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/frontend":/app -w /app node:20-alpine \
      sh -c 'npm install --no-audit --no-fund && npm run build' \
      || die "frontend build failed"
    ok "frontend built (via docker node container, owned by host user)"
  fi
else
  ok "skipping frontend rebuild (--no-build)"
fi

# ── 3. reload the stack ──
log "Reloading stack"
if [ "$NEEDS_IMAGE_BUILD" = true ]; then
  # New Python deps → rebuild the image and recreate containers.
  $COMPOSE up -d --build
else
  # Code-only change: `up -d` ensures containers exist; `restart` reloads the
  # bind-mounted Python so the running web/worker processes pick it up.
  $COMPOSE up -d
  $COMPOSE restart web worker nginx 2>/dev/null || $COMPOSE restart web worker
fi
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
