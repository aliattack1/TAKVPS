import asyncio

import asyncssh

from core.errors import AuthError, UnreachableError, translate
from core.events import EventBus
from storage.settings import SettingsStore


def test_translate_auth():
    assert isinstance(translate(asyncssh.PermissionDenied("x")), AuthError)


def test_translate_oserror():
    assert isinstance(translate(OSError("boom")), UnreachableError)


def test_bus_sync_and_async():
    got = []

    async def a(x):
        got.append(("a", x))

    bus = EventBus()
    bus.subscribe("t", lambda x: got.append(("s", x)))
    bus.subscribe("t", a)
    asyncio.run(bus.publish("t", 1))
    assert got == [("s", 1), ("a", 1)]


def test_settings_roundtrip(tmp_path):
    st = SettingsStore(tmp_path / "s.json")
    st.data.accent = "#123456"
    st.save()
    assert SettingsStore(tmp_path / "s.json").data.accent == "#123456"
