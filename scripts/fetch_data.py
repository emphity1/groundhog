# SPDX-License-Identifier: Apache-2.0
"""Download the registered datasets and record exactly what was downloaded.

Datasets never enter git. This script fetches them into ``data/raw/<dataset>/``,
verifies each file against the checksum the source itself publishes, and writes
``data/datasets.lock.json`` so every later result can name the data version it
used.

    python scripts/fetch_data.py --list
    python scripts/fetch_data.py opssat
    python scripts/fetch_data.py esa_adb --dry-run

Licences differ per dataset and attribution is mandatory for all of them — see
DATA.md before publishing anything built on this data.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "datasets.yaml"
DATA_ROOT = REPO_ROOT / "data" / "raw"
LOCK_PATH = REPO_ROOT / "data" / "datasets.lock.json"
ZENODO_API = "https://zenodo.org/api/records/{record_id}"
CHUNK = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class RemoteFile:
    name: str
    url: str
    size: int
    checksum: str  # as published by the source, e.g. "md5:abc123..."


def load_registry() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        registry: dict[str, Any] = yaml.safe_load(fh)["datasets"]
    return registry


def zenodo_files(record_id: str) -> list[RemoteFile]:
    """Ask Zenodo what the record contains. Checksums come from the source."""
    url = ZENODO_API.format(record_id=record_id)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = json.load(response)
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach Zenodo for record {record_id}: {exc}") from exc

    files = []
    for entry in payload.get("files", []):
        files.append(
            RemoteFile(
                name=entry["key"],
                url=entry["links"]["self"],
                size=int(entry["size"]),
                checksum=str(entry["checksum"]),
            )
        )
    if not files:
        raise SystemExit(f"Zenodo record {record_id} lists no files")
    return files


def digest(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path, checksum: str) -> bool:
    algorithm, _, expected = checksum.partition(":")
    if not expected:
        algorithm, expected = "md5", checksum
    try:
        return digest(path, algorithm) == expected
    except ValueError:
        print(f"  ! unknown checksum algorithm {algorithm!r}, cannot verify")
        return False


def download(remote: RemoteFile, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    done = 0
    with (
        urllib.request.urlopen(remote.url, timeout=120) as response,
        partial.open("wb") as fh,
    ):
        while chunk := response.read(CHUNK):
            fh.write(chunk)
            done += len(chunk)
            if remote.size:
                pct = 100 * done / remote.size
                print(f"\r  {remote.name}  {done / 1e6:8.1f} MB  {pct:5.1f}%", end="")
    print()
    partial.replace(target)


def fetch(name: str, spec: dict[str, Any], *, dry_run: bool, force: bool) -> list[dict[str, Any]]:
    if spec.get("source") != "zenodo":
        raise SystemExit(f"dataset {name!r}: only zenodo sources are supported so far")

    print(f"\n{name} — {spec['title']}")
    print(f"  licence {spec['licence']} · {spec.get('approx_size', 'unknown size')}")
    print("  attribution is mandatory, see DATA.md")

    remotes = zenodo_files(str(spec["record_id"]))
    wanted = set(spec.get("files") or [])
    if wanted:
        remotes = [r for r in remotes if r.name in wanted]
        missing = wanted - {r.name for r in remotes}
        if missing:
            print(f"  ! not present in the record: {', '.join(sorted(missing))}")

    entries: list[dict[str, Any]] = []
    for remote in remotes:
        target = DATA_ROOT / name / remote.name
        if dry_run:
            print(f"  would fetch {remote.name} ({remote.size / 1e6:.1f} MB)")
            continue

        if target.exists() and not force and verify(target, remote.checksum):
            print(f"  {remote.name}: already present and verified")
        else:
            download(remote, target)
            if not verify(target, remote.checksum):
                raise SystemExit(f"  ! checksum mismatch for {remote.name}, file left in place")
            print(f"  {remote.name}: verified")

        entries.append(
            {
                "file": remote.name,
                "size": remote.size,
                "checksum": remote.checksum,
                # POSIX separators, so the lock reads the same whichever OS wrote it
                "path": target.relative_to(REPO_ROOT).as_posix(),
            }
        )
    return entries


def write_lock(dataset: str, spec: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    lock: dict[str, Any] = {}
    if LOCK_PATH.exists():
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    lock[dataset] = {
        "doi": spec.get("doi"),
        "licence": spec["licence"],
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": entries,
    }
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"  recorded in {LOCK_PATH.relative_to(REPO_ROOT)}")


def main(argv: list[str] | None = None) -> int:
    # Dataset titles are not ASCII. On Windows a piped stdout (Git Bash, CI logs)
    # defaults to the ANSI code page, which garbles or rejects them.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", help="dataset key from configs/datasets.yaml")
    parser.add_argument("--list", action="store_true", help="show the registry and exit")
    parser.add_argument("--dry-run", action="store_true", help="resolve files, download nothing")
    parser.add_argument("--force", action="store_true", help="re-download even if verified")
    args = parser.parse_args(argv)

    registry = load_registry()

    if args.list or not args.dataset:
        print(f"{'key':14} {'licence':16} {'size':>14}  role")
        for key, spec in registry.items():
            print(
                f"{key:14} {spec['licence']:16} {spec.get('approx_size', '?'):>14}  {spec['role']}"
            )
        print("\nNothing is committed to git. See DATA.md for attribution requirements.")
        return 0

    if args.dataset not in registry:
        print(f"unknown dataset {args.dataset!r}; try --list", file=sys.stderr)
        return 2

    spec = registry[args.dataset]
    entries = fetch(args.dataset, spec, dry_run=args.dry_run, force=args.force)
    if entries:
        write_lock(args.dataset, spec, entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
