"""Download only model artifacts; no document data is sent to Hugging Face."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HOME", str(ROOT / ".cache/huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
from huggingface_hub import snapshot_download

config = json.loads((ROOT / "config/model.json").read_text())
repo = config["repo_id"]
manifest = ROOT / "models/manifest.json"
revision = config["revision"]
target = ROOT / config["local_directory"]
snapshot_download(repo_id=repo, revision=revision, local_dir=target, max_workers=4)
files = {}
for path in sorted(target.iterdir()):
    if path.is_file():
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        metadata = target / ".cache/huggingface/download" / (path.name + ".metadata")
        if metadata.exists():
            expected = metadata.read_text().splitlines()[1]
            if len(expected) == 64 and expected != digest:
                raise RuntimeError(f"SHA-256 mismatch: {path.name}")
        files[path.name] = {"bytes": path.stat().st_size, "sha256": digest}
manifest.write_text(json.dumps({"repo_id": repo, "revision": revision,
                               "files": files}, indent=2) + "\n")
print(json.dumps({"path": str(target), "revision": revision, "files": len(files)}))
