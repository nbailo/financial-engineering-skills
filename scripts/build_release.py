#!/usr/bin/env python3
"""Build the v0.5.0 release artifacts deterministically.

Two artifacts, matching the product boundary:

  financial-engineering-skills-<v>.tar.gz          the six installed skills
  financial-engineering-skills-advanced-<v>.tar.gz the two opt-in advanced beta skills

Determinism is the point: building twice from two clean checkouts of the same commit must
produce byte-identical output. Everything that normally varies is pinned.

  mtime      SOURCE_DATE_EPOCH, defaulting to the commit time of the release SHA
  uid/gid    0/0 with empty uname/gname, so the builder's account never leaks in
  order      sorted by path, because os.walk order is filesystem-dependent
  mode       0644 for regular files, 0755 for directories and executables, nothing else
  symlinks   stored as symlinks with their target recorded, never dereferenced
  gzip       mtime=0 in the member header, which is the usual reason two tarballs of
             identical content still differ byte for byte

Standard library only. No network.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.5.0"

DEFAULT_PATHS = ["skills", "LICENSE", "README.md", "CHANGELOG.md", "SECURITY.md", "AGENTS.md"]
ADVANCED_PATHS = ["advanced", "LICENSE"]


def commit_epoch(sha: str) -> int:
    out = subprocess.run(["git", "-C", str(ROOT), "show", "-s", "--format=%ct", sha],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out)


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        base = ROOT / p
        if base.is_file() or base.is_symlink():
            files.append(base)
        elif base.is_dir():
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames
                               if d not in {"__pycache__", ".git"} and not d.startswith(".")]
                for fn in filenames:
                    if fn.endswith((".pyc", ".pyo")) or fn == ".DS_Store":
                        continue
                    files.append(Path(dirpath) / fn)
    # Sorted by POSIX-relative path: os.walk order is not stable across filesystems.
    return sorted(set(files), key=lambda f: f.relative_to(ROOT).as_posix())


def normalize(ti: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    ti.mtime = epoch
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if ti.isdir():
        ti.mode = 0o755
    elif ti.issym():
        ti.mode = 0o777
    else:
        ti.mode = 0o755 if (ti.mode & 0o100) else 0o644
    return ti


def build(name: str, paths: list[str], epoch: int, outdir: Path) -> tuple[Path, list[dict]]:
    files = collect(paths)
    manifest: list[dict] = []
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            arc = f"{name}/{rel}"
            ti = tar.gettarinfo(str(f), arcname=arc)
            ti = normalize(ti, epoch)
            entry = {"path": rel, "mode": oct(ti.mode), "symlink": "", "sha256": "", "type": ""}
            if ti.issym():
                entry["type"] = "symlink"
                entry["symlink"] = ti.linkname
                entry["sha256"] = ""
                tar.addfile(ti)
            else:
                entry["type"] = "file"
                data = f.read_bytes()
                entry["sha256"] = hashlib.sha256(data).hexdigest()
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
            manifest.append(entry)

    out = outdir / f"{name}.tar.gz"
    # mtime=0 in the gzip header; otherwise the wrapper differs even when the tar matches.
    with open(out, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(raw.getvalue())
    return out, sorted(manifest, key=lambda e: e["path"])


def main() -> int:
    sha = sys.argv[1] if len(sys.argv) > 1 else subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "dist"
    outdir.mkdir(parents=True, exist_ok=True)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", commit_epoch(sha)))

    artifacts = []
    full_manifest = {}
    for name, paths in (
        (f"financial-engineering-skills-{VERSION}", DEFAULT_PATHS),
        (f"financial-engineering-skills-advanced-{VERSION}", ADVANCED_PATHS),
    ):
        path, manifest = build(name, paths, epoch, outdir)
        artifacts.append(path)
        full_manifest[name] = manifest

    man = outdir / f"manifest-{VERSION}.json"
    man.write_text(json.dumps(
        {"version": VERSION, "commit": sha, "source_date_epoch": epoch,
         "artifacts": full_manifest}, indent=2, sort_keys=True) + "\n")

    lines = []
    for a in sorted(artifacts + [man], key=lambda p: p.name):
        lines.append(f"{hashlib.sha256(a.read_bytes()).hexdigest()}  {a.name}")
    (outdir / f"SHA256SUMS-{VERSION}.txt").write_text("\n".join(lines) + "\n")

    for line in lines:
        print("  " + line)
    print(f"  commit {sha}  epoch {epoch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
