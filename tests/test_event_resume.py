"""Test that reconnects only ask the button for events not processed yet."""

import asyncio
import struct

from pyflic_ble.const import (
    OPCODE_BUTTON_EVENT,
    OPCODE_INIT_BUTTON_EVENTS_RESPONSE_WITH_BOOT_ID,
    TWIST_OPCODE_BUTTON_EVENT,
    TWIST_OPCODE_INIT_BUTTON_EVENTS_RESPONSE,
    PushTwistMode,
)
from pyflic_ble.handlers.base import DeviceProtocolHandler
from pyflic_ble.handlers.flic2 import Flic2ProtocolHandler
from pyflic_ble.handlers.twist import TwistProtocolHandler

BOOT_ID = 0x12345678


class InitTransport:
    """Record packets and answer the init request with a fixed response."""

    def __init__(self, init_response: bytes) -> None:
        """Initialize the transport."""
        self.packets: list[bytes] = []
        self._init_response = init_response

    async def write_gatt(self, uuid: str, data: bytes) -> None:
        """Record a raw GATT write."""
        self.packets.append(data)

    async def write_packet(self, data: bytes, authenticated: bool) -> None:
        """Record a packet."""
        self.packets.append(data)

    async def wait_for_opcode(self, opcode: int) -> bytes:
        """Answer the init request."""
        return self._init_response

    async def wait_for_opcodes(self, opcodes: list[int]) -> bytes:
        """Answer the init request."""
        return self._init_response


def _connect(handler: DeviceProtocolHandler, init_response: bytes) -> bytes:
    """Start a new connection and return the init request that was sent."""
    transport = InitTransport(init_response)
    handler.reset_state()
    handler.bind_transport(
        transport.write_gatt,
        transport.write_packet,
        transport.wait_for_opcode,
        transport.wait_for_opcodes,
    )
    asyncio.run(handler.init_button_events(None, None))
    return transport.packets[0]


def test_twist_reconnect_resumes_after_processed_events() -> None:
    """Test a Twist reconnect hands back the boot id and the last event count."""
    handler = TwistProtocolHandler(PushTwistMode.DEFAULT)
    init_response = (
        bytes([TWIST_OPCODE_INIT_BUTTON_EVENTS_RESPONSE])
        + bytes(6)
        + struct.pack("<II", 5, BOOT_ID)
        + bytes([2])
    )

    first_init = _connect(handler, init_response)
    assert struct.unpack_from("<I", first_init, 1)[0] == 0

    handler.handle_notification(
        bytes([TWIST_OPCODE_BUTTON_EVENT]) + struct.pack("<I", 9)
    )

    second_init = _connect(handler, init_response)
    assert struct.unpack_from("<I", second_init, 1)[0] == 9
    assert struct.unpack_from("<I", second_init, 70)[0] == BOOT_ID


def test_flic2_reconnect_resumes_after_processed_events() -> None:
    """Test a Flic 2 reconnect hands back the boot id and the last event count."""
    handler = Flic2ProtocolHandler()
    init_response = (
        bytes([0x00, OPCODE_INIT_BUTTON_EVENTS_RESPONSE_WITH_BOOT_ID])
        + bytes(6)
        + struct.pack("<II", 5, BOOT_ID)
    )

    first_init = _connect(handler, init_response)
    assert struct.unpack_from("<II", first_init, 2) == (0, 0)

    handler.handle_notification(bytes([0x00, OPCODE_BUTTON_EVENT]) + struct.pack("<I", 9))

    second_init = _connect(handler, init_response)
    assert struct.unpack_from("<II", second_init, 2) == (9, BOOT_ID)
