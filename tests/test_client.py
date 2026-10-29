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
from pyflic_ble.const import (
    OPCODE_DISCONNECT_VERIFIED_LINK,
    OPCODE_PING_REQUEST,
    OPCODE_PING_RESPONSE,
    TWIST_OPCODE_DISCONNECTED_VERIFIED_LINK,
)
from pyflic_ble.handlers.flic2 import Flic2ProtocolHandler


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


def _flic2_session(ble_client: MagicMock) -> FlicClient:
    """Return a Flic 2 client with an established session on connection 3."""
    client = FlicClient("AA:BB:CC:DD:EE:FF", device_type=DeviceType.FLIC2)
    client._client = ble_client
    client._state = SessionState.SESSION_ESTABLISHED
    client._connection_id = 3
    return client


def test_flic2_ping_is_answered() -> None:
    """Test a Flic 2 ping gets a response, so the button keeps the session."""
    write_packet = AsyncMock()

    async def run() -> FlicClient:
        client = _flic2_session(_connected_ble_client())
        client._write_packet = write_packet

        # Session packets end in a 5-byte MAC that the client strips
        client._handle_framed_notification(
            bytearray([3, OPCODE_PING_REQUEST]) + bytes(5)
        )
        await asyncio.sleep(0)
        return client

    client = asyncio.run(run())

    write_packet.assert_awaited_once_with(bytes([3, OPCODE_PING_RESPONSE]))
    assert client._response_queue.empty()


def test_flic2_disconnect_verified_link_drops_the_link() -> None:
    """Test the Flic 2 ending the verified session also drops the BLE link."""
    ble_client = _connected_ble_client()

    async def run() -> None:
        client = _flic2_session(ble_client)
        client._handle_framed_notification(
            bytearray([3, OPCODE_DISCONNECT_VERIFIED_LINK, 0]) + bytes(5)
        )
        await asyncio.sleep(0)

    asyncio.run(run())

    ble_client.disconnect.assert_awaited_once()


def test_flic2_init_never_auto_disconnects() -> None:
    """Test the Flic 2 init asks the button to never disconnect on idle."""
    write_packet = AsyncMock()
    handler = Flic2ProtocolHandler()
    handler.bind_transport(
        AsyncMock(),
        write_packet,
        AsyncMock(side_effect=TimeoutError),
        AsyncMock(side_effect=TimeoutError),
    )

    asyncio.run(handler.init_button_events(None, None))

    request = write_packet.await_args.args[0]
    assert int.from_bytes(request[10:18], byteorder="little") & 0x1FF == 511
