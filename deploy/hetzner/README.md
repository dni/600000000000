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
  --expected-source-commit "$COMMIT" \
  --expected-archive-sha256 <reviewed-archive-sha256> \
  --expected-release-sha256 <reviewed-RELEASE.json-sha256>
```

Build twice and require identical archive SHA-256 before deployment.

## Deployment

Deployment requires an independently reviewed exact commit and exact hashes supplied by the operator. Materialize the runner itself from the reviewed Git object and verify it before execution; do not execute the mutable worktree copy:

```bash
COMMIT=<full-reviewed-sha>
RUNNER="$HOME/.cache/600-wtf-deploy/deploy-$COMMIT.sh"
mkdir -p "$(dirname "$RUNNER")"
git show "$COMMIT:deploy/hetzner/deploy.sh" > "$RUNNER"
chmod 700 "$RUNNER"
printf '%s  %s\n' <reviewed-deployer-sha256> "$RUNNER" | sha256sum -c -

SOURCE_REPO="$PWD" \
EXPECTED_COMMIT="$COMMIT" \
EXPECTED_ARCHIVE_SHA256=<reviewed-archive-sha256> \
EXPECTED_RELEASE_SHA256=<reviewed-RELEASE.json-sha256> \
EXPECTED_BUILDER_SHA256=<reviewed-builder-sha256> \
EXPECTED_INSTALLER_SHA256=<reviewed-installer-sha256> \
HETZNER_PASS_FILE=/tmp/hetzner-deploy-pass.secret \
SSH_ASKPASS=/tmp/hetzner-askpass.sh \
bash "$RUNNER"
```

The reviewed runner ignores mutable worktree bytes. It materializes `EXPECTED_COMMIT` through `git archive` into a private snapshot, verifies the builder and installer before their first execution, rebuilds and validates the archive before exporting credential paths, uploads to a unique private directory, rehashes the remote upload, and runs the atomic installer without `sudo`.

## DNS activation

The static tree may be installed before DNS changes. Validate and activate the Caddy fragment transactionally only after its pinned target tree passes. Because Caddy cannot obtain a publicly trusted `600.wtf` certificate while authoritative DNS still points at the old host, update the authoritative DigitalOcean apex `A` record only after the filesystem/Caddy transaction passes; then wait for certificate issuance and verify public HTTPS, NIP-05 JSON, CORS, website assets, join regression probes, and rollback readiness.
