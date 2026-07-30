from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
TEST_TMP_ROOT = Path.home() / ".cache" / "sixhundred-release-tests"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
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
    def load_installer(self):
        spec = importlib.util.spec_from_file_location("sixhundred_installer", INSTALLER)
        if spec is None or spec.loader is None:
            raise AssertionError("installer import spec unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def build(self, output: Path, source_commit: str = SOURCE_COMMIT) -> subprocess.CompletedProcess[str]:
        return run(
            str(BUILDER),
            "--root",
            str(ROOT),
            "--output",
            str(output),
            "--source-commit",
            source_commit,
        )

    def test_release_build_is_deterministic_and_public_only(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as td:
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
                self.assertEqual(release["sourceCommit"], SOURCE_COMMIT)
                self.assertEqual(release["files"][".well-known/nostr.json"], sha256(ROOT / ".well-known/nostr.json"))
                self.assertEqual(release["files"]["index.html"], sha256(ROOT / "index.html"))
                self.assertEqual(set(PUBLIC_ROOT_FILES), {name for name in names if "/" not in name and name != "RELEASE.json"})
                self.assertEqual(PUBLIC_DIRS, {name.split("/", 1)[0] for name in names if "/" in name})

    def test_validator_accepts_release_and_rejects_extra_member(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as td:
            release = Path(td) / "release.tar.gz"
            self.build(release)
            result = run(
                str(INSTALLER),
                "--validate-only",
                str(release),
                "--expected-source-commit",
                SOURCE_COMMIT,
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
                SOURCE_COMMIT,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("unexpected archive member", failed.stderr)

    def test_validator_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as td:
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
                SOURCE_COMMIT,
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("unsafe archive member", failed.stderr)

    def test_archive_input_is_single_open_regular_file_bound(self) -> None:
        source = INSTALLER.read_text()
        self.assertIn("def read_regular_file(", source)
        self.assertIn("os.O_NOFOLLOW", source)
        self.assertIn("meta.st_nlink != 1", source)
        self.assertIn("files, release = read_archive_bytes(archive_bytes)", source)
        self.assertNotIn("read_archive(args.archive)", source)

        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temp:
            temp_path = Path(temp)
            archive = temp_path / "release.tar.gz"
            self.build(archive)
            for kind in ("symlink", "hardlink"):
                candidate = temp_path / f"{kind}.tar.gz"
                if kind == "symlink":
                    candidate.symlink_to(archive)
                else:
                    candidate.hardlink_to(archive)
                failed = run(
                    str(INSTALLER),
                    "--validate-only",
                    str(candidate),
                    "--expected-source-commit",
                    SOURCE_COMMIT,
                    check=False,
                )
                self.assertNotEqual(failed.returncode, 0)
                self.assertRegex(failed.stderr, r"unsafe archive|link")

    def test_main_validates_the_exact_bytes_it_hashed(self) -> None:
        module = self.load_installer()
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temp:
            base = Path(temp)
            approved = base / "approved.tar.gz"
            replaced_path = base / "replaced.tar.gz"
            self.build(approved)
            approved_bytes = approved.read_bytes()
            replaced_path.write_bytes(b"not the approved archive")
            argv = [
                str(INSTALLER),
                "--validate-only",
                str(replaced_path),
                "--expected-source-commit",
                SOURCE_COMMIT,
                "--expected-archive-sha256",
                hashlib.sha256(approved_bytes).hexdigest(),
            ]
            stdout = io.StringIO()
            with patch.object(module, "read_regular_file", return_value=approved_bytes), patch.object(sys, "argv", argv):
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(module.main(), 0)
            self.assertIn("validated", stdout.getvalue())

    def test_install_rejects_symlinked_ancestor_and_parent_retarget(self) -> None:
        module = self.load_installer()
        cache_root = Path.home() / ".cache" / "sixhundred-installer-tests"
        cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache_root) as temp:
            base = Path(temp)
            archive = base / "release.tar.gz"
            self.build(archive)
            files, release = module.read_archive_bytes(archive.read_bytes())

            real = base / "real"
            (real / "site-root").mkdir(parents=True)
            alias = base / "alias"
            alias.symlink_to(real, target_is_directory=True)
            setattr(module, "TARGET", alias / "site-root" / "600-wtf")
            with self.assertRaisesRegex(RuntimeError, "symlinked target ancestor"):
                module.install(files, release)

            parent = base / "anchored" / "site-root"
            parent.mkdir(parents=True)
            target = parent / "600-wtf"
            setattr(module, "TARGET", target)
            installed = module.install(files, release)
            self.assertFalse(installed["backupRetained"])
            before = (target / "index.html").read_bytes()
            original_same_inode = module.same_inode
            calls = 0

            def retarget_before_promotion(path, fd):
                nonlocal calls
                calls += 1
                if calls >= 2:
                    return False
                return original_same_inode(path, fd)

            with patch.object(module, "same_inode", retarget_before_promotion):
                with self.assertRaisesRegex(RuntimeError, "target parent path changed"):
                    module.install(files, release)
            self.assertEqual((target / "index.html").read_bytes(), before)
            self.assertFalse(list(parent.glob(".600-wtf-stage-*")))
            self.assertFalse(list(parent.glob(".600-wtf-backup-*")))

            original_rename = module.os.rename

            def fail_stage_promotion(source, destination, *args, **kwargs):
                if str(source).startswith(".600-wtf-stage-") and destination == "600-wtf":
                    raise OSError("injected stage promotion failure")
                return original_rename(source, destination, *args, **kwargs)

            with patch.object(module.os, "rename", fail_stage_promotion):
                with self.assertRaisesRegex(OSError, "injected stage promotion failure"):
                    module.install(files, release)
            self.assertEqual((target / "index.html").read_bytes(), before)
            self.assertFalse(list(parent.glob(".600-wtf-stage-*")))
            self.assertFalse(list(parent.glob(".600-wtf-backup-*")))

    def test_deployer_is_exact_commit_and_hash_gated(self) -> None:
        source = DEPLOYER.read_text()
        self.assertIn('${EXPECTED_COMMIT:?', source)
        self.assertIn('${EXPECTED_ARCHIVE_SHA256:?', source)
        self.assertIn('${EXPECTED_BUILDER_SHA256:?', source)
        self.assertIn('${EXPECTED_INSTALLER_SHA256:?', source)
        self.assertIn('git -C "$SOURCE_REPO" archive "$EXPECTED_COMMIT"', source)
        self.assertIn('SNAPSHOT_BUILDER_SHA256=$(sha256sum "$BUILDER"', source)
        self.assertIn('SNAPSHOT_INSTALLER_SHA256=$(sha256sum "$INSTALLER"', source)
        self.assertNotIn('git status --porcelain', source)
        self.assertIn('--expected-source-commit', source)
        self.assertIn('--expected-archive-sha256', source)
        self.assertNotIn('sudo ', source)
        builder_hash_gate = source.index('[[ "$SNAPSHOT_BUILDER_SHA256" == "$EXPECTED_BUILDER_SHA256" ]]')
        builder_execution = source.index('python3 "$BUILDER"')
        installer_hash_gate = source.index('[[ "$SNAPSHOT_INSTALLER_SHA256" == "$EXPECTED_INSTALLER_SHA256" ]]')
        installer_execution = source.index('python3 "$INSTALLER" --validate-only')
        credential_export = source.index('export SSH_ASKPASS=')
        self.assertLess(builder_hash_gate, builder_execution)
        self.assertLess(installer_hash_gate, installer_execution)
        self.assertLess(installer_execution, credential_export)
        self.assertIn('/dist/', (ROOT / '.gitignore').read_text().splitlines())
        runbook = (ROOT / 'deploy/hetzner/README.md').read_text()
        self.assertIn('git show "$COMMIT:deploy/hetzner/deploy.sh"', runbook)
        self.assertIn('<reviewed-deployer-sha256>', runbook)

    def test_deployer_ignores_assume_unchanged_worktree_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as temp:
            base = Path(temp)
            repo = base / "repo"
            shutil.copytree(
                ROOT,
                repo,
                ignore=shutil.ignore_patterns(".git", "node_modules", "dist", "test-results", "playwright-report", "__pycache__"),
            )
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.name", "release-test"], check=True)
            subprocess.run(["git", "-C", repo, "config", "user.email", "release-test@example.invalid"], check=True)
            subprocess.run(["git", "-C", repo, "add", "."], check=True)
            subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], check=True)
            commit = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
            builder = repo / "deploy/hetzner/build-release.py"
            installer = repo / "deploy/hetzner/install-release.py"
            builder_sha = sha256(builder)
            installer_sha = sha256(installer)
            expected_archive = base / "expected.tar.gz"
            subprocess.run(
                [sys.executable, builder, "--root", repo, "--output", expected_archive, "--source-commit", commit],
                check=True,
                text=True,
                capture_output=True,
            )
            archive_sha = sha256(expected_archive)
            builder.write_text("#!/usr/bin/env python3\nraise SystemExit('WORKTREE_BUILDER_EXECUTED')\n")
            subprocess.run(["git", "-C", repo, "update-index", "--assume-unchanged", "deploy/hetzner/build-release.py"], check=True)
            self.assertEqual(subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True), "")
            exact_deployer = subprocess.check_output(
                ["git", "-C", repo, "show", f"{commit}:deploy/hetzner/deploy.sh"], text=True
            )
            env = {
                **os.environ,
                "SOURCE_REPO": str(repo),
                "EXPECTED_COMMIT": commit,
                "EXPECTED_ARCHIVE_SHA256": archive_sha,
                "EXPECTED_BUILDER_SHA256": builder_sha,
                "EXPECTED_INSTALLER_SHA256": installer_sha,
                "PREPARE_ONLY": "1",
                "XDG_CACHE_HOME": str(base / "cache"),
            }
            prepared = subprocess.run(
                ["bash", "-s"], cwd=repo, input=exact_deployer, env=env, text=True, capture_output=True
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            self.assertIn("PREPARE_ONLY_OK", prepared.stdout)
            self.assertNotIn("WORKTREE_BUILDER_EXECUTED", prepared.stderr)

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
