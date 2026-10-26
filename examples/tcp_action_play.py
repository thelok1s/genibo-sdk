"""The TCP path that Ex20 had commented out, using GeniboNet.

Legacy (C#):
    m_CGenibo.Connect(txtIp.Text);              // six sockets, ports 6100..6105
    m_CGenibo.Auth("geniibo", "1234");
    m_CGenibo.ActionPlay(true, "7.act", false, false, 100);
    m_CGenibo.DisConnect();

The frames are golden-tested against the legacy code. Which Genibo models expose
this TCP service is not documented in the repository; treat it as unverified.

    uv run python examples/tcp_action_play.py 192.168.0.10
"""

from __future__ import annotations

import sys

from genibo.net import GeniboNet


def main(host: str) -> None:
    with GeniboNet(host) as net:
        verdicts = net.auth("geniibo", "1234")
        print("auth replies per channel:", {ch.name: v for ch, v in verdicts.items()})
        net.action_play("7.act", master=True, sound=False, emoticon=False, speed_percent=100)
        print("sent ActionPlay 7.act")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: tcp_action_play.py <robot-ip>")
    main(sys.argv[1])
