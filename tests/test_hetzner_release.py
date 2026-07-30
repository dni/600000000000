from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "deploy/hetzner/build-release.py"
INSTALLER = ROOT / "deploy/hetzner/install-release.py"
DEPLOYER = ROOT / "deploy/hetzner/deploy.sh"
CADDY_FRAGMENT = ROOT / "deploy/hetzner/Caddyfile.fragment"
PUBLIC_ROOT_FILES = {
    "404.html",
    "business-cards.html",
    "favicon.ico",
    "index.html",
    "inscriptions.html",
    "liquid.html",
    "matrix.html",
    "members.json",
    "onchain.html",
    "ord.html",
    "ordinals.html",
    "qrcode-sticker.svg",
    "signal.html",
    "sticker.html",
    "style.css",
}
PUBLIC_DIRS = {".well-known", "blocks", "img", "inscriptions", "vendor"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


class HetznerReleaseTests(unittest.TestCase):
    def build(self, output: Path) -> subprocess.CompletedProcess[str]:
        return run(
            str(BUILDER),
            "--root",
            str(ROOT),
            "--output",
            str(output),
            "--source-commit",
            "a3a36a524fe4433d5674650b7e7c8f28c2e0e05b",
        )

    def test_release_build_is_deterministic_and_public_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            first = Path(td) / "first.tar.gz"
            second = Path(td) / "second.tar.gz"
            self.build(first)
            self.build(second)
            self.assertEqual(sha256(first), sha256(second))

            with tarfile.open(first, "r:gz") as archive:
                members = archive.getmembers()
                names = [member.name for member in members]
                self.assertEqual(names, sorted(names))
                self.assertIn("RELEASE.json", names)
                self.assertIn(".well-known/nostr.json", names)
                self.assertIn("index.html", names)
                self.assertIn("members.json", names)
                self.assertNotIn("Dockerfile", names)
                self.assertFalse(any(name.startswith((".git/", "deploy/", "docs/", "tests/")) for name in names))
                self.assertFalse(any(name in {"package.json", "package-lock.json", "playwright.config.ts", "tsconfig.json"} for name in names))
                for member in members:
                    self.assertFalse(member.issym() or member.islnk())
                    self.assertEqual(member.uid, 0)
                    self.assertEqual(member.gid, 0)
                    self.assertEqual(member.mtime, 0)
                    self.assertIn(member.mode, {0o644, 0o755})

                release_file = archive.extractfile("RELEASE.json")
                self.assertIsNotNone(release_file)
                assert release_file is not None
                release = json.load(release_file)
                self.assertEqual(release["schema"], "600-wtf-static-release/v1")
                self.assertEqual(release["sourceCommit"], "a3a36a524fe4433d5674650b7e7c8f28c2e0e05b")
                self.assertEqual(release["files"][".well-known/nostr.json"], sha256(ROOT / ".well-known/nostr.json"))
                self.assertEqual(release["files"]["index.html"], sha256(ROOT / "index.html"))
                self.assertEqual(set(PUBLIC_ROOT_FILES), {name for name in names if "/" not in name and name != "RELEASE.json"})
                self.assertEqual(PUBLIC_DIRS, {name.split("/", 1)[0] for name in names if "/" in name})

    def test_validator_accepts_release_and_rejects_extra_member(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            release = Path(td) / "release.tar.gz"
            self.build(release)
            result = run(
                str(INSTALLER),
                "--validate-only",
                str(release),
                "--expected-source-commit",
                "a3a36a524fe4433d5674650b7e7c8f28c2e0e05b",
            )
            self.assertIn("validated", result.stdout)

            tampered = Path(td) / "tampered.tar.gz"
            with tarfile.open(release, "r:gz") as source, tarfile.open(tampered, "w:gz") as destination:
                for member in source.getmembers():
                    extracted = source.extractfile(member) if member.isfile() else None
                    data = extracted.read() if extracted is not None else None
                    destination.addfile(member, io.BytesIO(data) if data is not None else None)
                payload = b"not public\n"
                member = tarfile.TarInfo("secret.txt")
                member.size = len(payload)
                member.mode = 0o644
                destination.addfile(member, io.BytesIO(payload))
            failed = run(
                str(INSTALLER),
                "--validate-only",
                str(tampered),
                "--expected-source-commit",
                "a3a36a524fe4433d5674650b7e7c8f28c2e0e05b",
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("unexpected archive member", failed.stderr)

    def test_validator_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            attack = Path(td) / "attack.tar.gz"
            with tarfile.open(attack, "w:gz") as archive:
                payload = b"escape"
                member = tarfile.TarInfo("../escape")
                member.size = len(payload)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(payload))
            failed = run(
                str(INSTALLER),
                "--validate-only",
                str(attack),
                "--expected-source-commit",
                "a3a36a524fe4433d5674650b7e7c8f28c2e0e05b",
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("unsafe archive member", failed.stderr)

    def test_deployer_is_exact_commit_and_hash_gated(self) -> None:
        source = DEPLOYER.read_text()
        self.assertIn('${EXPECTED_COMMIT:?', source)
        self.assertIn('${EXPECTED_ARCHIVE_SHA256:?', source)
        self.assertIn('${EXPECTED_INSTALLER_SHA256:?', source)
        self.assertIn('git status --porcelain', source)
        self.assertIn('build-release.py', source)
        self.assertIn('--expected-source-commit', source)
        self.assertIn('--expected-archive-sha256', source)
        self.assertNotIn('sudo ', source)
        self.assertIn('/dist/', (ROOT / '.gitignore').read_text().splitlines())

    def test_caddy_fragment_serves_apex_and_nip05_with_cors(self) -> None:
        source = CADDY_FRAGMENT.read_text()
        self.assertIn('600.wtf {', source)
        self.assertIn('root * /srv/site/600-wtf', source)
        self.assertIn('/.well-known/*', source)
        self.assertIn('Access-Control-Allow-Origin "*"', source)
        self.assertIn('Content-Type "application/json; charset=utf-8"', source)
        self.assertIn('/RELEASE.json', source)
        self.assertNotIn('/index.html', source.split('try_files', 1)[-1] if 'try_files' in source else '')

    def test_container_image_copies_only_public_allowlist(self) -> None:
        source = (ROOT / 'Dockerfile').read_text()
        self.assertNotIn('COPY . /usr/share/nginx/html', source)
        for directory in sorted(PUBLIC_DIRS):
            self.assertIn(f'COPY {directory} /usr/share/nginx/html/{directory}', source)
        self.assertNotIn('package.json', source)
        self.assertNotIn('deploy/', source)


if __name__ == "__main__":
    unittest.main()
