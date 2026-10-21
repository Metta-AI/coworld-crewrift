"""Capture the actual authoring CLI command without a build, upload, or provider call."""

import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
probe = Path(sys.argv[2]).resolve()
root.mkdir(mode=0o700)
bin_dir = root / "bin"
bin_dir.mkdir()
(root / "tufte.css").write_text("")
for command in ["uv", "docker"]:
    stub = bin_dir / command
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['UPLOAD_COMMAND_CAPTURE']).open('a') as output:\n"
        " output.write(json.dumps({'command':Path(sys.argv[0]).name,'args':sys.argv[1:]})+'\\n')\n"
        "print('source fixture: no build or upload performed')\n"
        "sys.exit(78 if Path(sys.argv[0]).name=='uv' else 0)\n"
    )
    stub.chmod(0o700)
for label, model, options in [
    ("default", "anthropic/claude-haiku-4.5", []),
    ("configured", "checkpoint/" + "a" * 64, []),
    (
        "explicit",
        "anthropic/claude-sonnet-4.6",
        ["--llm-model", "anthropic/claude-sonnet-4.6"],
    ),
]:
    capture = root / f"{label}.jsonl"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "UPLOAD_COMMAND_CAPTURE": str(capture),
        "BEDROCK_MODEL": "retired-provider-model-must-not-win",
    }
    env.pop("COWORLD_LLM_MODEL", None)
    if label == "configured":
        env["COWORLD_LLM_MODEL"] = model
    result = subprocess.run(
        [
            str(probe),
            "--coworld-dir",
            str(root),
            "--tufte-dir",
            str(root),
            "--image-tag",
            "owned-upload-fixture:never-built",
            *options,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    commands = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [c["command"] for c in commands] == ["docker", "uv"]
    args = commands[1]["args"]
    assert args[:3] == ["run", "coworld", "upload-policy"]
    assert "--use-llm" in args and args[args.index("--llm-model") + 1] == model
    assert "transport=native_messages" in args
    assert not any("bedrock" in arg.lower() for arg in args)
    assert "source fixture: no build or upload performed" in result.stdout
print(
    "actual authoring CLI: native flags; default/configured/explicit models; zero builds/uploads/providers"
)
