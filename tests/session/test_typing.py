"""Phase 6 milestone: BASIC commands can be typed (design.md §8, §9).

Uses the real ROMs (skipped when they are missing).
"""

from __future__ import annotations

import pytest

from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session, SessionError
from tests.machines import CONFIG_464, run_until_ready

CONFIG = CONFIG_464
pytestmark = pytest.mark.skipif(
    CONFIG.lower_rom is None or not CONFIG.lower_rom.exists, reason="ROMs missing"
)


@pytest.fixture(name="session")
def fixture_session() -> Session:
    """A CPC 464 booted to BASIC."""
    session = Session(CPC464(CONFIG))
    run_until_ready(session)
    return session


def visible(session: Session) -> list[str]:
    """The non-empty screen lines, without a line holding only the cursor block."""
    return [line for line in session.screen_text()["lines"] if line and line != "█"]


def test_print_command(session: Session) -> None:
    """The design's example: typing PRINT 1+1 and RETURN makes BASIC print 2."""
    result = session.type_text("PRINT 1+1\r")
    assert result["reason"] == "typed"
    assert visible(session)[-3:] == ["PRINT 1+1", " 2", "Ready"]


def test_program_with_symbols(session: Session) -> None:
    """A program with quotes, colons, semicolons and lower case runs."""
    session.type_text('10 FOR i=1 TO 3:PRINT "Hi";i*2:NEXT\rRUN\r')
    session.run(max_us=500_000, timeout_s=30)
    assert visible(session)[-6:] == [
        '10 FOR i=1 TO 3:PRINT "Hi";i*2:NEXT',
        "RUN",
        "Hi 2",
        "Hi 4",
        "Hi 6",
        "Ready",
    ]


def test_key_press_and_release(session: Session) -> None:
    """A key held with key_press is seen by the firmware; key_release lets it go."""
    session.key_press("shift")
    session.key_press("a")
    session.run(max_us=60_000)
    session.key_release("all")
    session.run(max_us=200_000)
    assert visible(session)[-1].startswith("A")
    assert session.devices_get()["keys_held"] == []


def test_untypable_text(session: Session) -> None:
    """Characters without a CPC key are rejected before anything is typed."""
    with pytest.raises(SessionError, match="no CPC key types"):
        session.type_text("Grüße")
    with pytest.raises(SessionError, match="unknown key"):
        session.key_press("hyper")
