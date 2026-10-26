"""Genibo SDK: Python-first tooling for the Genibo robot.

* :class:`Genibo` - live client for the Bluetooth serial link
* :mod:`genibo.program` - compiled programs (Python -> verified command plan)
* :mod:`genibo.sim` - protocol-level simulator (not hardware evidence)
* :mod:`genibo.protocol` - wire format, golden-tested against the legacy C#
"""

__version__ = "0.1.0"

from genibo.errors import (
    BuildError,
    Diagnostic,
    GeniboError,
    JointError,
    ResponseTimeout,
    RobotStatusError,
    SafetyLatchError,
    TransportError,
)
from genibo.joints import Joint, JointMap, load_profile
from genibo.protocol.constants import Storage, Torque
from genibo.robot import DEFAULT_ROBOT_ID, Genibo, Led

__all__ = [
    "DEFAULT_ROBOT_ID",
    "BuildError",
    "Diagnostic",
    "Genibo",
    "GeniboError",
    "Joint",
    "JointError",
    "JointMap",
    "Led",
    "ResponseTimeout",
    "RobotStatusError",
    "SafetyLatchError",
    "Storage",
    "Torque",
    "TransportError",
    "__version__",
    "load_profile",
]
