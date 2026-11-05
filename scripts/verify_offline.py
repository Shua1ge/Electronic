"""Run via scripts/offline-python to validate network denial and Metal together."""
import errno
import json
import socket
from pathlib import Path

try:
    socket.create_connection(("1.1.1.1", 443), timeout=2)
except OSError as error:
    assert error.errno in (errno.EPERM, errno.EACCES), error
    result = {"network_denied": True, "errno": error.errno}
else:
    raise RuntimeError("Network access was not blocked")

import mlx.core as mx
x = mx.ones((8, 8))
assert mx.sum(x @ x).item() == 512
result["metal_compute_passed"] = True
out = Path(__file__).resolve().parents[1] / "outputs/verification/offline.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, indent=2) + "\n")
print(result)
