"""Test the Flic client connection lifecycle."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from bleak import BleakError
import pytest

from pyflic_ble import (
    DeviceType,
    FlicAuthenticationError,
    FlicClient,
    FlicProtocolError,
)
from pyflic_ble.client import SessionState
from pyflic_ble.const import TWIST_OPCODE_DISCONNECTED_VERIFIED_LINK


def _twist_client() -> FlicClient:
    """Return a paired Twist client that has not connected yet."""
    return FlicClient(
        "AA:BB:CC:DD:EE:FF",
        ble_device=MagicMock(),
        pairing_id=1,
        pairing_key=bytes(16),
        device_type=DeviceType.TWIST,
    )


def _connected_ble_client() -> MagicMock:
    """Return a stand-in for a connected bleak client."""
    ble_client = MagicMock()
    ble_client.is_connected = True
    ble_client.mtu_size = 517
    ble_client.start_notify = AsyncMock()
    ble_client.disconnect = AsyncMock()
    return ble_client


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
    client = _twist_client()

    with caplog.at_level(logging.DEBUG), pytest.raises(FlicProtocolError):
        asyncio.run(client.start())

    assert [
        record.getMessage() for record in caplog.records if record.levelno > logging.DEBUG
    ] == []


def test_failed_start_drops_the_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test a start that fails after connecting disconnects, so a retry starts clean."""
    ble_client = _connected_ble_client()
    monkeypatch.setattr(
        "pyflic_ble.client.close_stale_connections_by_address", AsyncMock()
    )
    monkeypatch.setattr(
        "pyflic_ble.client.establish_connection", AsyncMock(return_value=ble_client)
    )
    client = _twist_client()
    client._request_connection_parameters = AsyncMock()
    client.quick_verify = AsyncMock(
        side_effect=FlicAuthenticationError("Quick verify timeout")
    )

    with pytest.raises(FlicAuthenticationError):
        asyncio.run(client.start())

    ble_client.disconnect.assert_awaited_once()
    assert not client.is_connected


def test_disconnected_verified_link_drops_the_link() -> None:
    """Test the Twist ending the verified session also drops the BLE link."""
    ble_client = _connected_ble_client()

    async def run() -> None:
        client = _twist_client()
        client._client = ble_client
        client._state = SessionState.SESSION_ESTABLISHED

        # Session packets end in a 5-byte MAC that the client strips
        client._handle_unframed_notification(
            bytearray([TWIST_OPCODE_DISCONNECTED_VERIFIED_LINK, 0]) + bytes(5)
        )
        await asyncio.sleep(0)

    asyncio.run(run())

    ble_client.disconnect.assert_awaited_once()
