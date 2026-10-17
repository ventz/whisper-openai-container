"""Build-time step: download a pinned model revision and verify every file.

Usage: python fetch_model.py MODEL_NAME OUTPUT_DIR
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(name, output_dir):
    lock = json.loads((Path(__file__).parent / "models.lock.json").read_text())
    if name not in lock:
        sys.exit(f"unknown model {name!r}; pinned models: {', '.join(lock)}")
    entry = lock[name]
    out = Path(output_dir)
    snapshot_download(
        entry["repo"],
        revision=entry["revision"],
        local_dir=out,
        allow_patterns=list(entry["files"]),
    )
    shutil.rmtree(out / ".cache", ignore_errors=True)
    present = {p.name for p in out.iterdir()}
    if present != set(entry["files"]):
        sys.exit(f"{name}: expected files {sorted(entry['files'])}, got {sorted(present)}")
    for filename, expected in entry["files"].items():
        actual = sha256(out / filename)
        if actual != expected:
            sys.exit(f"{name}: sha256 mismatch for {filename}: {actual}")
    print(f"{name} @ {entry['revision'][:12]}: {len(entry['files'])} files verified")


if __name__ == "__main__":
    main(*sys.argv[1:3])
