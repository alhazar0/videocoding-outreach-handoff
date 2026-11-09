#!/usr/bin/env python3
"""Reassemble and verify the encrypted handoff package. Does not decrypt it."""
from pathlib import Path
import hashlib
import json
import os
import sys
import tempfile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
    manifest = json.loads((root / "parts-manifest.json").read_text(encoding="utf-8"))
    basename = "videocoding-operations-20261101.encrypted.zip"
    if manifest["output"] != basename:
        raise ValueError("Unexpected output filename")
    for i, part in enumerate(manifest["parts"], 1):
        if part["name"] != f"{basename}.{i:03d}":
            raise ValueError("Invalid part sequence or filename")
        p = root / part["name"]
        if not p.is_file() or p.stat().st_size != part["bytes"]:
            raise ValueError(f"Missing or incomplete part: {p.name}")
    if len(manifest["parts"]) != 8:
        raise ValueError("Expected all eight parts")
    output = root / basename
    if output.exists():
        if output.stat().st_size == manifest["bytes"] and digest(output) == manifest["sha256"]:
            print(f"Already verified: {output}")
            return
        raise ValueError("Output already exists but does not match; move it aside first")
    fd, temp_name = tempfile.mkstemp(prefix="handoff-join-", suffix=".tmp", dir=root)
    temp = Path(temp_name)
    total = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as dest:
            for part in manifest["parts"]:
                part_hash = hashlib.sha256()
                with (root / part["name"]).open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        part_hash.update(chunk)
                        total.update(chunk)
                        dest.write(chunk)
                if part_hash.hexdigest() != part["sha256"]:
                    raise ValueError(f"Checksum mismatch: {part['name']}")
                print(f"Verified {part['name']}", flush=True)
        if temp.stat().st_size != manifest["bytes"] or total.hexdigest() != manifest["sha256"]:
            raise ValueError("Combined file checksum or size mismatch")
        if output.exists():
            raise ValueError("Output appeared during assembly; refusing to overwrite")
        temp.rename(output)
        print(f"Verified package: {output}")
        print("Open it in an AES ZIP compatible tool. Obtain the password separately.")
    finally:
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(f"Cannot assemble package: {exc}")
