#!/usr/bin/env python3
"""Validate and atomically install a 600.wtf static-site release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import tarfile


TARGET = Path("/home/deploy/bimCVP/infra/site-root/600-wtf")
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
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
HASH_RE = re.compile(r"[0-9a-f]{64}")
MAX_FILES = 5000
MAX_FILE_SIZE = 64 * 1024 * 1024
MAX_TOTAL_SIZE = 256 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and path.as_posix() == name


def allowed_name(name: str) -> bool:
    if name == "RELEASE.json" or name in PUBLIC_ROOT_FILES:
        return True
    head = name.split("/", 1)[0]
    return "/" in name and head in PUBLIC_DIRS


def read_archive(archive_path: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise RuntimeError("unsafe release archive")
    files: dict[str, bytes] = {}
    total = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_FILES:
            raise RuntimeError("archive file-count limit exceeded")
        for member in members:
            if not safe_name(member.name):
                raise RuntimeError(f"unsafe archive member: {member.name}")
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError(f"non-regular archive member: {member.name}")
            if member.name in files:
                raise RuntimeError(f"duplicate archive member: {member.name}")
            if not allowed_name(member.name):
                raise RuntimeError(f"unexpected archive member: {member.name}")
            if member.size < 0 or member.size > MAX_FILE_SIZE:
                raise RuntimeError(f"archive member size invalid: {member.name}")
            total += member.size
            if total > MAX_TOTAL_SIZE:
                raise RuntimeError("archive expanded-size limit exceeded")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"cannot read archive member: {member.name}")
            data = extracted.read(MAX_FILE_SIZE + 1)
            if len(data) != member.size:
                raise RuntimeError(f"archive member size mismatch: {member.name}")
            files[member.name] = data

    if "RELEASE.json" not in files:
        raise RuntimeError("release manifest missing")
    try:
        release = json.loads(files["RELEASE.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("release manifest invalid") from exc
    if not isinstance(release, dict) or release.get("schema") != "600-wtf-static-release/v1":
        raise RuntimeError("release schema invalid")
    source_commit = release.get("sourceCommit")
    manifest_files = release.get("files")
    if not isinstance(source_commit, str) or not COMMIT_RE.fullmatch(source_commit):
        raise RuntimeError("release source commit invalid")
    if not isinstance(manifest_files, dict):
        raise RuntimeError("release file manifest invalid")
    actual_names = set(files) - {"RELEASE.json"}
    if set(manifest_files) != actual_names:
        raise RuntimeError("release file manifest scope mismatch")
    for name in sorted(actual_names):
        expected = manifest_files.get(name)
        if not isinstance(expected, str) or not HASH_RE.fullmatch(expected) or digest(files[name]) != expected:
            raise RuntimeError(f"release file hash mismatch: {name}")

    for required in ("index.html", "members.json", ".well-known/nostr.json"):
        if required not in files:
            raise RuntimeError(f"required public file missing: {required}")
    try:
        nostr = json.loads(files[".well-known/nostr.json"])
        members = json.loads(files["members.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("public JSON invalid") from exc
    if not isinstance(nostr, dict) or not isinstance(nostr.get("names"), dict):
        raise RuntimeError("nostr.json names map invalid")
    if not isinstance(members, (dict, list)):
        raise RuntimeError("members.json invalid")
    return files, release


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install(files: dict[str, bytes], release: dict[str, object]) -> dict[str, object]:
    manifest_files = release.get("files")
    if not isinstance(manifest_files, dict):
        raise RuntimeError("release file manifest invalid at install")
    parent = TARGET.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise RuntimeError("unsafe target parent")
    if TARGET.exists() and (TARGET.is_symlink() or not TARGET.is_dir()):
        raise RuntimeError("unsafe existing target")

    stage = parent / f".600-wtf-stage-{secrets.token_hex(16)}"
    backup = parent / f".600-wtf-backup-{secrets.token_hex(16)}"
    stage.mkdir(mode=0o755)
    promoted = False
    backed_up = False
    try:
        for name, data in sorted(files.items()):
            destination = stage.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o644)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise RuntimeError(f"short write: {name}")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            if digest(destination.read_bytes()) != digest(data):
                raise RuntimeError(f"staged hash mismatch: {name}")
        fsync_directory(stage)
        if TARGET.exists():
            TARGET.rename(backup)
            backed_up = True
        stage.rename(TARGET)
        promoted = True
        fsync_directory(parent)
        if digest((TARGET / "index.html").read_bytes()) != manifest_files["index.html"]:
            raise RuntimeError("promoted index hash mismatch")
        if digest((TARGET / ".well-known/nostr.json").read_bytes()) != manifest_files[".well-known/nostr.json"]:
            raise RuntimeError("promoted nostr hash mismatch")
    except BaseException:
        if promoted and TARGET.exists():
            failed = parent / f".600-wtf-failed-{secrets.token_hex(16)}"
            TARGET.rename(failed)
            shutil.rmtree(failed, ignore_errors=True)
        if backed_up and backup.exists():
            backup.rename(TARGET)
        fsync_directory(parent)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    return {"ok": True, "target": str(TARGET), "sourceCommit": release["sourceCommit"], "files": len(files)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-archive-sha256")
    args = parser.parse_args()
    try:
        if bool(args.validate_only) == bool(args.install):
            raise RuntimeError("choose exactly one of --validate-only or --install")
        if not COMMIT_RE.fullmatch(args.expected_source_commit):
            raise RuntimeError("expected source commit invalid")
        archive_bytes = args.archive.read_bytes()
        if args.expected_archive_sha256:
            if not HASH_RE.fullmatch(args.expected_archive_sha256) or digest(archive_bytes) != args.expected_archive_sha256:
                raise RuntimeError("archive SHA-256 mismatch")
        elif args.install:
            raise RuntimeError("install requires --expected-archive-sha256")
        files, release = read_archive(args.archive)
        if release["sourceCommit"] != args.expected_source_commit:
            raise RuntimeError("release source commit mismatch")
        if args.validate_only:
            print(f"validated {len(files)} public release files")
        else:
            print(json.dumps(install(files, release), sort_keys=True))
    except Exception as exc:
        parser.exit(1, f"install failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
