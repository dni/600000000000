# Hetzner deployment for 600.wtf

This directory packages the public static website as a deterministic archive and installs it atomically at:

```text
/home/deploy/bimCVP/infra/site-root/600-wtf
```

Caddy sees that directory as `/srv/site/600-wtf`. `Caddyfile.fragment` is a reviewed fragment for the existing shared Caddy configuration; it is not installed by `deploy.sh` because proxy changes use a separate privileged, rollback-capable transaction.

## Release contract

The archive contains only the explicit public allowlist:

- public root HTML/CSS/JSON/image files;
- `.well-known/`;
- `blocks/`, `img/`, `inscriptions/`, and `vendor/`;
- generated `RELEASE.json` with the source commit and SHA-256 of every retained file.

Repository metadata, tests, deployment code, package metadata, and documentation are excluded. Symlinks, traversal, unexpected files, duplicate members, oversized members, and hash drift fail closed.

## Build and validate

```bash
COMMIT=$(git rev-parse HEAD)
python3 deploy/hetzner/build-release.py \
  --root . \
  --output dist/600-wtf-release.tar.gz \
  --source-commit "$COMMIT"

python3 deploy/hetzner/install-release.py \
  --validate-only dist/600-wtf-release.tar.gz \
  --expected-source-commit "$COMMIT"
```

Build twice and require identical archive SHA-256 before deployment.

## Deployment

Deployment requires an independently reviewed exact commit and exact hashes supplied by the operator:

```bash
EXPECTED_COMMIT=<full-reviewed-sha> \
EXPECTED_ARCHIVE_SHA256=<sha256> \
EXPECTED_INSTALLER_SHA256=<sha256> \
HETZNER_PASS_FILE=/tmp/hetzner-deploy-pass.secret \
SSH_ASKPASS=/tmp/hetzner-askpass.sh \
./deploy/hetzner/deploy.sh
```

The wrapper refuses a dirty or mismatched worktree, rebuilds the archive, verifies all hashes, uploads to a unique private directory, rehashes the remote upload, and runs the atomic installer without `sudo`.

## DNS activation

The static tree may be installed before DNS changes. Activate the Caddy fragment transactionally, verify the origin with `curl --resolve 600.wtf:443:<origin-ip>`, then change the authoritative DigitalOcean `A` record for `600.wtf` only after origin HTTPS, NIP-05 JSON, CORS, website assets, and rollback have passed.
