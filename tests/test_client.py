"""Test the Flic client connection lifecycle."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from bleak import BleakError
import pytest

from pyflic_ble import DeviceType, FlicClient, FlicProtocolError


def test_failed_connection_attempt_logs_only_debug(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test an out of range button does not log above debug on every attempt."""
    monkeypatch.setattr(
        "pyflic_ble.client.close_stale_connections_by_address", AsyncMock()
    )
    monkeypatch.setattr(
        "pyflic_ble.client.establish_connection",
        AsyncMock(side_effect=BleakError("Device not found")),
    )
    client = FlicClient(
        "AA:BB:CC:DD:EE:FF",
        ble_device=MagicMock(),
        pairing_id=1,
        pairing_key=bytes(16),
        device_type=DeviceType.TWIST,
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(FlicProtocolError):
        asyncio.run(client.start())

    assert [
        record.getMessage() for record in caplog.records if record.levelno > logging.DEBUG
    ] == []
