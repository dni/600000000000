#!/usr/bin/env python3
"""Build a deterministic, public-only 600.wtf static-site archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import tempfile


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


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name in sorted(PUBLIC_ROOT_FILES):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"missing or unsafe public file: {name}")
        files[name] = path.read_bytes()
    for directory in sorted(PUBLIC_DIRS):
        base = root / directory
        if base.is_symlink() or not base.is_dir():
            raise RuntimeError(f"missing or unsafe public directory: {directory}")
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"symlink forbidden in public tree: {path.relative_to(root)}")
            if path.is_dir():
                continue
            if not path.is_file():
                raise RuntimeError(f"non-regular public entry: {path.relative_to(root)}")
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def build(root: Path, output: Path, source_commit: str) -> dict[str, object]:
    if not COMMIT_RE.fullmatch(source_commit):
        raise RuntimeError("source commit must be a full lowercase SHA-1")
    root = root.resolve(strict=True)
    files = public_files(root)
    release = {
        "schema": "600-wtf-static-release/v1",
        "sourceCommit": source_commit,
        "files": {name: digest(data) for name, data in sorted(files.items())},
    }
    files["RELEASE.json"] = (json.dumps(release, sort_keys=True, separators=(",", ":")) + "\n").encode()

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{output.name}.", dir=output.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        with gzip.GzipFile(filename="", mode="wb", fileobj=temporary, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
                for name, data in sorted(files.items()):
                    archive.addfile(tar_info(name, len(data)), io.BytesIO(data))
        temporary.flush()
    temporary_path.chmod(0o600)
    temporary_path.replace(output)
    archive_sha256 = digest(output.read_bytes())
    return {"archiveSha256": archive_sha256, "files": len(files), "sourceCommit": source_commit}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        result = build(args.root, args.output, args.source_commit)
    except Exception as exc:
        parser.exit(1, f"build failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
