from __future__ import annotations

import pytest

from crewborg.native import NativeSession, PlayerRegistration


@pytest.fixture
def native_session(tmp_path):
    return NativeSession(
        PlayerRegistration(requested_slot=None),
        tmp_path / "native.jsonl",
        lambda generation: None,
    )
