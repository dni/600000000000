#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
cd "$ROOT"
: "${EXPECTED_COMMIT:?Set the independently reviewed full commit SHA}"
: "${EXPECTED_ARCHIVE_SHA256:?Set the independently reproduced release SHA-256}"
: "${EXPECTED_INSTALLER_SHA256:?Set the independently reviewed installer SHA-256}"
[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$EXPECTED_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]]
[[ "$EXPECTED_INSTALLER_SHA256" =~ ^[0-9a-f]{64}$ ]]
[[ $(git rev-parse HEAD) == "$EXPECTED_COMMIT" ]] || { echo 'reviewed commit mismatch' >&2; exit 1; }
[[ -z $(git status --porcelain) ]] || { echo 'working tree is not clean' >&2; exit 1; }

HOST=${SIXHUNDRED_DEPLOY_HOST:-deploy@178.105.93.78}
PASS_FILE=${HETZNER_PASS_FILE:-/tmp/hetzner-deploy-pass.secret}
ASKPASS=${SSH_ASKPASS:-/tmp/hetzner-askpass.sh}
[[ -f "$PASS_FILE" && ! -L "$PASS_FILE" && $(stat -c '%a' "$PASS_FILE") == 600 ]] || { echo 'unsafe deploy password file' >&2; exit 1; }
[[ -x "$ASKPASS" && ! -L "$ASKPASS" ]] || { echo 'unsafe SSH askpass helper' >&2; exit 1; }
export SSH_ASKPASS="$ASKPASS" SSH_ASKPASS_REQUIRE=force HETZNER_PASS_FILE="$PASS_FILE" DISPLAY=:0
SSH_OPTS=(-T -o BatchMode=no -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=1 -o ConnectTimeout=10 -o StrictHostKeyChecking=yes)
SCP_OPTS=(-q -o BatchMode=no -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=1 -o ConnectTimeout=10 -o StrictHostKeyChecking=yes)

mkdir -p dist
ARCHIVE=dist/600-wtf-release.tar.gz
INSTALLER=deploy/hetzner/install-release.py
python3 deploy/hetzner/build-release.py --root . --output "$ARCHIVE" --source-commit "$EXPECTED_COMMIT" >/dev/null
python3 "$INSTALLER" --validate-only "$ARCHIVE" --expected-source-commit "$EXPECTED_COMMIT" --expected-archive-sha256 "$EXPECTED_ARCHIVE_SHA256"
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | cut -d ' ' -f1)
INSTALLER_SHA256=$(sha256sum "$INSTALLER" | cut -d ' ' -f1)
[[ "$ARCHIVE_SHA256" == "$EXPECTED_ARCHIVE_SHA256" ]] || { echo 'release archive drifted' >&2; exit 1; }
[[ "$INSTALLER_SHA256" == "$EXPECTED_INSTALLER_SHA256" ]] || { echo 'installer drifted' >&2; exit 1; }

UPLOAD_DIR="/home/deploy/.600-wtf-upload-$(openssl rand -hex 16)"
cleanup_remote() { ssh "${SSH_OPTS[@]}" "$HOST" "rm -rf -- '$UPLOAD_DIR'" >/dev/null 2>&1 || true; }
trap cleanup_remote EXIT
ssh "${SSH_OPTS[@]}" "$HOST" "install -d -m 700 '$UPLOAD_DIR'"
scp "${SCP_OPTS[@]}" "$ARCHIVE" "$HOST:$UPLOAD_DIR/600-wtf-release.tar.gz"
scp "${SCP_OPTS[@]}" "$INSTALLER" "$HOST:$UPLOAD_DIR/install-release.py"
ssh "${SSH_OPTS[@]}" "$HOST" "set -e; chmod 600 '$UPLOAD_DIR/'*; test \"\$(sha256sum '$UPLOAD_DIR/600-wtf-release.tar.gz' | cut -d ' ' -f1)\" = '$ARCHIVE_SHA256'; test \"\$(sha256sum '$UPLOAD_DIR/install-release.py' | cut -d ' ' -f1)\" = '$INSTALLER_SHA256'"

mkdir -p /home/flx/backups/600-wtf
chmod 700 /home/flx/backups/600-wtf
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG=/home/flx/backups/600-wtf/600-wtf-deploy-$STAMP.log
ssh "${SSH_OPTS[@]}" "$HOST" \
  "python3 '$UPLOAD_DIR/install-release.py' --install '$UPLOAD_DIR/600-wtf-release.tar.gz' --expected-source-commit '$EXPECTED_COMMIT' --expected-archive-sha256 '$ARCHIVE_SHA256'" \
  | tee "$LOG"
chmod 600 "$LOG"
sha256sum "$LOG" > "$LOG.sha256"
chmod 600 "$LOG.sha256"
trap - EXIT
cleanup_remote
printf 'DEPLOY_LOG=%s\nSOURCE_COMMIT=%s\nARCHIVE_SHA256=%s\nINSTALLER_SHA256=%s\n' \
  "$LOG" "$EXPECTED_COMMIT" "$ARCHIVE_SHA256" "$INSTALLER_SHA256"
