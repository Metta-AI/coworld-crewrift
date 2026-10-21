"""The independent Aaln package must load its shared native owner at startup."""

import asyncio
import sys

import pytest

pytestmark = pytest.mark.asyncio


async def test_policy_entrypoint_imports_in_a_cold_interpreter():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import players.crewrift.crewborg.coworld.policy_player",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    assert stdout == b""
