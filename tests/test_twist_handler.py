"""Test the Flic Twist protocol handler."""

import asyncio
import struct

from pyflic_ble import DeviceType, FlicClient
from pyflic_ble.const import (
    EVENT_TYPE_TWIST_INCREMENT,
    TWIST_OPCODE_TWIST_EVENT,
    PushTwistMode,
)
from pyflic_ble.handlers.twist import TwistProtocolHandler
from pyflic_ble.rotate_tracker import D360


class FakeTransport:
    """Record the packets a handler writes."""

    def __init__(self) -> None:
        """Initialize the transport."""
        self.packets: list[bytes] = []

    async def write_gatt(self, uuid: str, data: bytes) -> None:
        """Record a raw GATT write."""
        self.packets.append(data)

    async def write_packet(self, data: bytes, authenticated: bool) -> None:
        """Record a packet."""
        self.packets.append(data)

    async def wait_for_opcode(self, opcode: int) -> bytes:
        """Act as if the device never answers."""
        raise TimeoutError

    async def wait_for_opcodes(self, opcodes: list[int]) -> bytes:
        """Act as if the device never answers."""
        raise TimeoutError


def _connect(handler: TwistProtocolHandler) -> FakeTransport:
    """Start a new connection and send the init request."""
    transport = FakeTransport()
    handler.reset_state()
    handler.bind_transport(
        transport.write_gatt,
        transport.write_packet,
        transport.wait_for_opcode,
        transport.wait_for_opcodes,
    )
    asyncio.run(handler.init_button_events(None, None))
    return transport


def _init_positions(packet: bytes) -> list[int]:
    """Return the 13 mode positions of an InitButtonEvents request."""
    return [
        struct.unpack_from("<I", packet, 5 + 5 * index)[0] >> 16 for index in range(13)
    ]


def _new_min(packet: bytes) -> int:
    """Return the new_min of an UpdateTwistPosition request."""
    return int.from_bytes(packet[2:8], byteorder="little", signed=True)


def _twist(handler: TwistProtocolHandler, mode_index: int, delta: int) -> list[str]:
    """Feed a one-direction twist to the handler and return the event types."""
    packet = (
        bytes([TWIST_OPCODE_TWIST_EVENT, mode_index])
        + (delta & 0xFFFFFF).to_bytes(3, byteorder="little")
        + (max(delta, 0) & 0xFFFFFF).to_bytes(3, byteorder="little")
        + (min(delta, 0) & 0xFFFFFF).to_bytes(3, byteorder="little")
        + bytes(2)
    )
    _, rotate_events, _ = handler.handle_notification(packet)
    return [event.event_type for event in rotate_events]


def test_position_write_after_reconnect_uses_device_frame() -> None:
    """Test a position write after a reconnect lands where the device expects it."""
    handler = TwistProtocolHandler(PushTwistMode.DEFAULT)
    restored = D360 // 2
    target = D360 * 3 // 4

    _connect(handler)
    handler.build_update_twist_position(0, restored)
    _connect(handler)

    # The init restarted the device at min 0 and position `restored`
    request = handler.build_update_twist_position(0, target)
    assert _new_min(request) == restored - target


def test_init_sends_exact_positions() -> None:
    """Test the init hands back the stored position without rounding loss."""
    handler = TwistProtocolHandler(PushTwistMode.DEFAULT)
    restored = D360 * 40 // 100

    _connect(handler)
    handler.build_update_twist_position(0, restored)
    transport = _connect(handler)

    assert _init_positions(transport.packets[0])[0] == restored


def test_position_write_without_session_is_sent_with_next_init() -> None:
    """Test a position write without a session reaches the device on the next init."""
    client = FlicClient("AA:BB:CC:DD:EE:FF", device_type=DeviceType.TWIST)

    asyncio.run(client.async_send_update_twist_position(0, 50.0))

    transport = _connect(client._handler)
    assert _init_positions(transport.packets[0])[0] == D360 // 2


def test_no_increment_burst_after_position_write() -> None:
    """Test a small twist after a position write emits a single increment."""
    handler = TwistProtocolHandler(PushTwistMode.DEFAULT)
    _connect(handler)
    _twist(handler, 0, D360 // 10)

    handler.build_update_twist_position(0, round(D360 * 0.55))

    assert _twist(handler, 0, D360 // 100 + 1) == [EVENT_TYPE_TWIST_INCREMENT]


def test_first_twist_after_reconnect_emits_increment() -> None:
    """Test the first 1% step after a reconnect is not swallowed."""
    handler = TwistProtocolHandler(PushTwistMode.DEFAULT)
    _connect(handler)
    _twist(handler, 0, D360 // 10)
    _connect(handler)

    assert _twist(handler, 0, D360 // 100 + 1) == [EVENT_TYPE_TWIST_INCREMENT]


def test_position_write_rounds_to_nearest_unit() -> None:
    """Test a percentage is converted to the nearest unit instead of truncated."""
    client = FlicClient("AA:BB:CC:DD:EE:FF", device_type=DeviceType.TWIST)

    asyncio.run(client.async_send_update_twist_position(0, 55.0))

    transport = _connect(client._handler)
    assert _init_positions(transport.packets[0])[0] == round(D360 * 0.55)
