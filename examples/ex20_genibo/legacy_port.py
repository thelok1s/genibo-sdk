"""Ex20_Genibo translated line by line with the compatibility shim.

This is the first step of a migration: keep the legacy calls, swap the language.
The shim transmits the same bytes as the C# class (golden-tested). Move to
``genibo.Genibo`` afterwards (see src/main.py).

    uv run python examples/ex20_genibo/legacy_port.py            # simulator
    uv run python examples/ex20_genibo/legacy_port.py COM5       # robot
"""

from __future__ import annotations

import sys

from genibo.legacy import CGeniboBt


def make_shim(port: str | None) -> tuple[CGeniboBt, str | int]:
    if port:
        return CGeniboBt(), port  # real Bluetooth SPP serial port
    from genibo.sim import SimTransport, VirtualGenibo

    dev = VirtualGenibo(robot_id=0x20)
    dev.add_file(0, "7.act", b"(opaque)")
    return CGeniboBt(lambda _port, _baud: SimTransport(dev)), 5


def main() -> None:
    m_CGenibo, port = make_shim(sys.argv[1] if len(sys.argv) > 1 else None)
    txtMotion = "7.act"

    # private void btnMove_Click(...)
    if m_CGenibo.connect(port, 115200):
        m_CGenibo.Cmd_Bluetooth_Play(0x20, 0, txtMotion)
        m_CGenibo.request_motion_is_playing()
        m_CGenibo.wait(500)
        print("is playing:", m_CGenibo.get_is_playing())
        m_CGenibo.disconnect()
    else:
        print("could not open", port)


if __name__ == "__main__":
    main()
