"""Exercise ordinary CLI admission, rather than only its internal policy seam."""

import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
probe = Path(sys.argv[2]).resolve()
root.mkdir(mode=0o700)
for origin in ["native", "teacher"]:
    env = {
        **os.environ,
        "NOTSUS_POLICY_ORIGIN": origin,
        "COWORLD_PLAYER_ARTIFACT_UPLOAD_URL": (root / f"{origin}.zip").as_uri(),
    }
    env.pop("COWORLD_LLM_ENDPOINT", None)
    result = subprocess.run(
        [str(probe), "--url:ws://127.0.0.1:1/player?slot=0&token=private-fixture"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    (root / f"{origin}.stdout").write_text(result.stdout)
    (root / f"{origin}.stderr").write_text(result.stderr)
    assert result.returncode != 0  # The fixture deliberately has no game server.
    if origin == "native":
        assert "COWORLD_LLM_ENDPOINT is required" in result.stderr
        assert "scripted_teacher" not in result.stdout
    else:
        assert "notsus policy: scripted_teacher" in result.stdout
        assert "COWORLD_LLM_ENDPOINT is required" not in result.stderr
    assert "private-fixture" not in result.stdout + result.stderr
print(
    "actual CLI: native missing endpoint rejects; teacher proceeds to socket admission; public token redacted"
)
