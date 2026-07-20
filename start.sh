#!/usr/bin/env bash
#
# start.sh — one-shot bootstrap for OpenCode Scanner.
#
# Detects where it's run from, installs everything needed (Docker, Docker
# Compose, Node.js), generates secrets, builds the frontend, and brings the
# whole stack up. The web UI is served on port 12211 by default.
#
# Usage:
#   ./start.sh                 # install deps + build + start (port 12211)
#   WEB_PORT=8080 ./start.sh   # use a different web port
#   ./start.sh --no-build      # skip frontend rebuild (faster restarts)
#   ./start.sh --migrate-from /path/to/source/.env   # adopt source SECRET_KEY +
#                                                    # ENCRYPTION_KEY so a restored
#                                                    # backup's proxy/machine creds
#                                                    # decrypt correctly
#   ./start.sh --down          # stop the stack
#   ./start.sh --logs          # follow logs
#
set -euo pipefail

# ── Resolve project directory (this script's location) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

WEB_PORT="${WEB_PORT:-12211}"

# ── Pretty output ──
if [ -t 1 ]; then
  RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; BLU=$'\033[0;34m'; BLD=$'\033[1m'; RST=$'\033[0m'
else
  RED=""; GRN=""; YLW=""; BLU=""; BLD=""; RST=""
fi
log()  { echo "${BLU}==>${RST} ${BLD}$*${RST}"; }
ok()   { echo "${GRN}  ✓${RST} $*"; }
warn() { echo "${YLW}  !${RST} $*"; }
err()  { echo "${RED}  ✗${RST} $*" >&2; }
die()  { err "$*"; exit 1; }

# ── sudo helper (no-op if already root) ──
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else
    warn "Not root and 'sudo' not found — dependency installs may fail."
  fi
fi

# ── Detect OS / package manager ──
OS="$(uname -s)"
PKG=""
if [ "$OS" = "Linux" ]; then
  if   command -v apt-get >/dev/null 2>&1; then PKG="apt"
  elif command -v dnf     >/dev/null 2>&1; then PKG="dnf"
  elif command -v yum     >/dev/null 2>&1; then PKG="yum"
  elif command -v pacman  >/dev/null 2>&1; then PKG="pacman"
  elif command -v apk     >/dev/null 2>&1; then PKG="apk"
  fi
fi

pkg_install() {
  # pkg_install <generic-names...>
  case "$PKG" in
    apt)    $SUDO apt-get update -y -qq && $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" ;;
    dnf)    $SUDO dnf install -y -q "$@" ;;
    yum)    $SUDO yum install -y -q "$@" ;;
    pacman) $SUDO pacman -Sy --noconfirm "$@" ;;
    apk)    $SUDO apk add --no-cache "$@" ;;
    *)      warn "Unknown package manager — please install manually: $*" ;;
  esac
}

# ── Handle flags ──
case "${1:-}" in
  --down)  log "Stopping stack..."; docker compose down; exit 0 ;;
  --logs)  docker compose logs -f; exit 0 ;;
  --migrate-from) MIGRATE_FROM="${2:-}"; [ -z "$MIGRATE_FROM" ] && die "usage: $0 --migrate-from /path/to/source/.env" ;;
esac
NO_BUILD=false
[ "${1:-}" = "--no-build" ] && NO_BUILD=true

echo "${BLD}${BLU}"
echo "  OpenCode Scanner — bootstrap"
echo "  dir : $SCRIPT_DIR"
echo "  os  : $OS ($PKG)"
echo "  port: $WEB_PORT"
echo "${RST}"

# ── 1. Base tools (curl, git) ──
log "Checking base tools"
command -v curl >/dev/null 2>&1 || { warn "installing curl"; pkg_install curl; }
command -v git  >/dev/null 2>&1 || { warn "installing git";  pkg_install git;  }
ok "base tools present"

# ── 2. Docker ──
log "Checking Docker"
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker not found — installing via get.docker.com"
  curl -fsSL https://get.docker.com | $SUDO sh || die "Docker install failed"
  $SUDO systemctl enable --now docker 2>/dev/null || true
  # allow the current user to run docker without sudo (takes effect next login)
  if [ "$(id -u)" -ne 0 ]; then $SUDO usermod -aG docker "$USER" 2>/dev/null || true; fi
fi
# Verify the daemon is reachable
if ! docker info >/dev/null 2>&1; then
  $SUDO systemctl start docker 2>/dev/null || true
  if ! docker info >/dev/null 2>&1; then
    # fall back to sudo docker for the rest of this run
    if $SUDO docker info >/dev/null 2>&1; then
      warn "Using 'sudo docker' for this run (re-login to use docker without sudo)"
      DOCKER="$SUDO docker"
    else
      die "Docker daemon not reachable. Start it and re-run."
    fi
  else
    DOCKER="docker"
  fi
else
  DOCKER="docker"
fi
ok "Docker: $($DOCKER --version)"

# ── 3. Docker Compose (v2 plugin) ──
log "Checking Docker Compose"
if $DOCKER compose version >/dev/null 2>&1; then
  COMPOSE="$DOCKER compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  warn "Compose plugin missing — installing"
  pkg_install docker-compose-plugin 2>/dev/null || pkg_install docker-compose 2>/dev/null || true
  if $DOCKER compose version >/dev/null 2>&1; then COMPOSE="$DOCKER compose"
  else die "Docker Compose not available — install docker-compose-plugin manually"; fi
fi
ok "Compose: $($COMPOSE version | head -1)"

# ── 4. Node.js + npm (for building the frontend) ──
if [ "$NO_BUILD" = false ]; then
  log "Checking Node.js"
  if ! command -v npm >/dev/null 2>&1; then
    warn "Node.js not found — installing (NodeSource LTS)"
    if [ "$PKG" = "apt" ]; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO -E bash - >/dev/null 2>&1 || true
      pkg_install nodejs
    else
      pkg_install nodejs npm || true
    fi
  fi
  command -v npm >/dev/null 2>&1 && ok "Node: $(node -v), npm: $(npm -v)" || warn "npm still missing — will try to build frontend inside Docker if configured"
fi

# ── 5. Generate .env with secrets (once) ──
log "Configuring environment (.env)"
gen_secret() { openssl rand -hex 24 2>/dev/null || head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# Migration helper: copy SECRET_KEY/ENCRYPTION_KEY from another instance's .env
# so a restored DB backup decrypts correctly. Usage: --migrate-from /path/to/source/.env
if [ -n "${MIGRATE_FROM:-}" ]; then
  if [ ! -f "$MIGRATE_FROM" ]; then die "--migrate-from: file not found: $MIGRATE_FROM"; fi
  SRC_SECRET="$(grep -E '^SECRET_KEY=' "$MIGRATE_FROM" | cut -d= -f2-)"
  SRC_ENC="$(grep -E '^ENCRYPTION_KEY=' "$MIGRATE_FROM" | cut -d= -f2-)"
  [ -z "$SRC_ENC" ] && die "--migrate-from: no ENCRYPTION_KEY found in $MIGRATE_FROM"
  # write/overwrite local .env adopting the source's keys (random PG password)
  PGPASS="$(gen_secret | cut -c1-24)"
  cat > .env <<EOF
# Adopted from $MIGRATE_FROM so restored credentials decrypt (git-ignored)
WEB_PORT=${WEB_PORT}
SECRET_KEY=${SRC_SECRET}
ENCRYPTION_KEY=${SRC_ENC}
POSTGRES_USER=scanner
POSTGRES_PASSWORD=${PGPASS}
POSTGRES_DB=opencode_scanner
EOF
  ok "adopted SECRET_KEY + ENCRYPTION_KEY from $MIGRATE_FROM (proxy/machine creds will decrypt)"
fi

if [ ! -f .env ]; then
  SECRET_KEY="$(gen_secret)"
  ENCRYPTION_KEY="$(gen_secret)"
  PGPASS="$(gen_secret | cut -c1-24)"
  cat > .env <<EOF
# Generated by start.sh — keep this file secret (it's git-ignored)
WEB_PORT=${WEB_PORT}
SECRET_KEY=${SECRET_KEY}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
POSTGRES_USER=scanner
POSTGRES_PASSWORD=${PGPASS}
POSTGRES_DB=opencode_scanner
EOF
  ok "wrote .env with fresh random secrets"
  # Warn if a backup exists: restoring it needs the SOURCE's ENCRYPTION_KEY
  if ls backups/*.sql.gz >/dev/null 2>&1; then
    warn "A backup exists in backups/. If you restore it, the proxy/machine"
    warn "passwords won't decrypt unless ENCRYPTION_KEY matches the source."
    warn "Re-run:  ./start.sh --migrate-from /path/to/source/.env"
  fi
else
  # Keep existing secrets, just make sure WEB_PORT reflects the requested port
  if grep -q '^WEB_PORT=' .env; then
    sed -i "s/^WEB_PORT=.*/WEB_PORT=${WEB_PORT}/" .env
  else
    echo "WEB_PORT=${WEB_PORT}" >> .env
  fi
  ok ".env already exists (kept secrets, WEB_PORT=${WEB_PORT})"
fi

# ── 6. Build the frontend ──
if [ "$NO_BUILD" = false ] && command -v npm >/dev/null 2>&1; then
  log "Building frontend"
  ( cd frontend && npm install --no-audit --no-fund --silent && npm run build --silent )
  ok "frontend built → frontend/dist"
else
  [ -d frontend/dist ] && ok "using existing frontend/dist" || warn "frontend/dist missing and build skipped — UI may be blank"
fi

# ── 7. Bring the stack up ──
log "Starting the stack (this builds the image on first run — may take a few minutes)"
$COMPOSE up --build -d

# ── 8. Wait for health ──
log "Waiting for the API to become healthy"
API_UP=false
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:${WEB_PORT}/api/health" >/dev/null 2>&1; then API_UP=true; break; fi
  sleep 2
done

echo
if [ "$API_UP" = true ]; then
  ok "${GRN}Stack is up!${RST}"
else
  warn "API didn't answer in time — check logs: ${BLD}./start.sh --logs${RST}"
fi

# ── Access info ──
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "${BLD}${GRN}OpenCode Scanner is ready${RST}"
echo "  Local:   ${BLD}http://localhost:${WEB_PORT}${RST}"
[ -n "${HOST_IP:-}" ] && echo "  Network: ${BLD}http://${HOST_IP}:${WEB_PORT}${RST}"
echo
echo "  First time? Create an admin user:"
echo "    ${BLD}curl -X POST http://localhost:${WEB_PORT}/api/auth/register \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"username\":\"admin\",\"email\":\"admin@example.com\",\"password\":\"changeme\"}'${RST}"
echo
echo "  Manage: ${BLD}./start.sh --logs${RST} | ${BLD}./start.sh --down${RST} | ${BLD}./start.sh --no-build${RST}"
