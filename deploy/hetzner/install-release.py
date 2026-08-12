#!/usr/bin/env python3
"""Validate and atomically install a 600.wtf static-site release."""

from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
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
    "lore.html",
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
MAX_ARCHIVE_SIZE = 64 * 1024 * 1024
MAX_FILES = 5000
MAX_FILE_SIZE = 64 * 1024 * 1024
MAX_TOTAL_SIZE = 256 * 1024 * 1024
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


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


def read_regular_file(path: Path, limit: int = MAX_ARCHIVE_SIZE) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise RuntimeError("unsafe archive path") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise RuntimeError("unsafe archive link or file type")
        if meta.st_size < 0 or meta.st_size > limit:
            raise RuntimeError("archive size limit exceeded")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise RuntimeError("archive size limit exceeded")
        data = b"".join(chunks)
        if len(data) != meta.st_size:
            raise RuntimeError("archive changed while reading")
        return data
    finally:
        os.close(fd)


def read_archive_bytes(archive_bytes: bytes) -> tuple[dict[str, bytes], dict[str, object]]:
    files: dict[str, bytes] = {}
    total = 0
    try:
        archive_context = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise RuntimeError("release archive invalid") from exc
    with archive_context as archive:
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


def open_anchored_directory(path: Path) -> int:
    if not path.is_absolute():
        raise RuntimeError("target parent must be absolute")
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def same_inode(path: Path, fd: int) -> bool:
    try:
        by_path = os.stat(path, follow_symlinks=False)
        by_fd = os.fstat(fd)
    except OSError:
        return False
    return by_path.st_dev == by_fd.st_dev and by_path.st_ino == by_fd.st_ino


def fsync_fd(fd: int) -> None:
    os.fsync(fd)


def open_child_directory(parent_fd: int, name: str) -> int:
    return os.open(name, DIR_FLAGS, dir_fd=parent_fd)


def write_member(stage_fd: int, name: str, data: bytes) -> None:
    parts = PurePosixPath(name).parts
    current_fd = os.dup(stage_fd)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o755, dir_fd=current_fd)
            except FileExistsError:
                pass
            next_fd = open_child_directory(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        fd = os.open(
            parts[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o644,
            dir_fd=current_fd,
        )
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
        fsync_fd(current_fd)
    finally:
        os.close(current_fd)


def read_member(root_fd: int, name: str) -> bytes:
    parts = PurePosixPath(name).parts
    current_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = open_child_directory(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=current_fd)
        try:
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size > MAX_FILE_SIZE:
                raise RuntimeError(f"unsafe installed file: {name}")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(1024 * 1024, MAX_FILE_SIZE + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise RuntimeError(f"installed file too large: {name}")
            data = b"".join(chunks)
            if len(data) != meta.st_size:
                raise RuntimeError(f"installed file changed while reading: {name}")
            return data
        finally:
            os.close(fd)
    finally:
        os.close(current_fd)


def list_tree(root_fd: int, prefix: str = "") -> set[str]:
    names: set[str] = set()
    with os.scandir(root_fd) as entries:
        for entry in entries:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            if entry.is_symlink():
                raise RuntimeError(f"installed symlink rejected: {relative}")
            if entry.is_dir(follow_symlinks=False):
                child_fd = open_child_directory(root_fd, entry.name)
                try:
                    names.update(list_tree(child_fd, relative))
                finally:
                    os.close(child_fd)
            elif entry.is_file(follow_symlinks=False):
                names.add(relative)
            else:
                raise RuntimeError(f"installed special file rejected: {relative}")
    return names


def verify_tree(root_fd: int, files: dict[str, bytes]) -> None:
    if list_tree(root_fd) != set(files):
        raise RuntimeError("installed tree membership mismatch")
    for name, expected in sorted(files.items()):
        if digest(read_member(root_fd, name)) != digest(expected):
            raise RuntimeError(f"installed file hash mismatch: {name}")


def name_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def require_directory_at(parent_fd: int, name: str) -> None:
    meta = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(meta.st_mode):
        raise RuntimeError("unsafe existing target")


def remove_tree_at(parent_fd: int, name: str) -> None:
    child_fd = open_child_directory(parent_fd, name)
    try:
        with os.scandir(child_fd) as entries:
            for entry in entries:
                if entry.is_symlink() or entry.is_file(follow_symlinks=False):
                    os.unlink(entry.name, dir_fd=child_fd)
                elif entry.is_dir(follow_symlinks=False):
                    remove_tree_at(child_fd, entry.name)
                else:
                    raise RuntimeError(f"cannot remove special file: {entry.name}")
        fsync_fd(child_fd)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)
    fsync_fd(parent_fd)


def install(files: dict[str, bytes], release: dict[str, object]) -> dict[str, object]:
    manifest_files = release.get("files")
    if not isinstance(manifest_files, dict):
        raise RuntimeError("release file manifest invalid at install")
    parent = TARGET.parent
    target_name = TARGET.name
    try:
        parent_fd = open_anchored_directory(parent)
    except OSError as exc:
        raise RuntimeError("symlinked target ancestor or unsafe target parent") from exc
    stage_name = f".600-wtf-stage-{secrets.token_hex(16)}"
    backup_name = f".600-wtf-backup-{secrets.token_hex(16)}"
    failed_name = f".600-wtf-failed-{secrets.token_hex(16)}"
    stage_created = False
    promoted = False
    backed_up = False
    backup_retained = False
    try:
        if not same_inode(parent, parent_fd):
            raise RuntimeError("target parent path changed")
        if name_exists(parent_fd, target_name):
            require_directory_at(parent_fd, target_name)
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_created = True
        stage_fd = open_child_directory(parent_fd, stage_name)
        try:
            for name, data in sorted(files.items()):
                write_member(stage_fd, name, data)
            fsync_fd(stage_fd)
            verify_tree(stage_fd, files)
        finally:
            os.close(stage_fd)

        if not same_inode(parent, parent_fd):
            raise RuntimeError("target parent path changed")
        if name_exists(parent_fd, target_name):
            os.rename(target_name, backup_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            backed_up = True
        if not same_inode(parent, parent_fd):
            raise RuntimeError("target parent path changed")
        os.rename(stage_name, target_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        stage_created = False
        promoted = True
        fsync_fd(parent_fd)

        target_fd = open_child_directory(parent_fd, target_name)
        try:
            verify_tree(target_fd, files)
        finally:
            os.close(target_fd)
        if not same_inode(parent, parent_fd):
            raise RuntimeError("target parent path changed")
        if backed_up:
            try:
                remove_tree_at(parent_fd, backup_name)
                backed_up = False
            except OSError:
                backup_retained = True
        fsync_fd(parent_fd)
    except BaseException:
        if promoted and name_exists(parent_fd, target_name):
            os.rename(target_name, failed_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            promoted = False
            remove_tree_at(parent_fd, failed_name)
        if backed_up and name_exists(parent_fd, backup_name):
            os.rename(backup_name, target_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            backed_up = False
        if stage_created and name_exists(parent_fd, stage_name):
            remove_tree_at(parent_fd, stage_name)
            stage_created = False
        fsync_fd(parent_fd)
        raise
    finally:
        os.close(parent_fd)

    return {
        "ok": True,
        "target": str(TARGET),
        "sourceCommit": release["sourceCommit"],
        "files": len(files),
        "backupRetained": backup_retained,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-archive-sha256")
    parser.add_argument("--expected-release-sha256")
    args = parser.parse_args()
    try:
        if bool(args.validate_only) == bool(args.install):
            raise RuntimeError("choose exactly one of --validate-only or --install")
        if not COMMIT_RE.fullmatch(args.expected_source_commit):
            raise RuntimeError("expected source commit invalid")
        archive_bytes = read_regular_file(args.archive)
        if args.expected_archive_sha256:
            if not HASH_RE.fullmatch(args.expected_archive_sha256) or digest(archive_bytes) != args.expected_archive_sha256:
                raise RuntimeError("archive SHA-256 mismatch")
        elif args.install:
            raise RuntimeError("install requires --expected-archive-sha256")
        files, release = read_archive_bytes(archive_bytes)
        release_bytes = files["RELEASE.json"]
        if args.expected_release_sha256:
            if not HASH_RE.fullmatch(args.expected_release_sha256) or digest(release_bytes) != args.expected_release_sha256:
                raise RuntimeError("release manifest SHA-256 mismatch")
        elif args.install:
            raise RuntimeError("install requires --expected-release-sha256")
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
