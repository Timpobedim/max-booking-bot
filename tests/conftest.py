from __future__ import annotations

from collections.abc import Iterator

import pytest

from maxkit import MaxApi
from maxkit.testing import MaxEmulator


@pytest.fixture
def emulator() -> MaxEmulator:
    return MaxEmulator()


@pytest.fixture
def api(emulator: MaxEmulator) -> Iterator[MaxApi]:
    with MaxApi(emulator.token, transport=emulator.transport(), sleep=lambda _: None) as client:
        yield client
