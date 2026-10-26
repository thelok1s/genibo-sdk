"""Protocol constants for the Genibo host links.

Every value here is taken from ``OpenJigWare/COjw_25_Genibo.cs`` (verbatim copy in ``compat/legacy/``)
(class ``Ojw.CGenibo_Bt`` for the Bluetooth serial link, ``Ojw.CGenibo`` for TCP)
unless the comment names another file. The legacy names are kept in comments so
the two code bases can be cross-referenced.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag

#: Every link frame (Bluetooth serial and TCP) starts with one 0xFF byte.
LINK_HEADER = 0xFF
#: Servo-bus / MPSU packets start with two 0xFF bytes.
BUS_HEADER = b"\xff\xff"

#: Robot / device ID that addresses every device (``0xfe`` in the legacy code).
BROADCAST_ID = 0xFE
#: Factory default servo ID mentioned in legacy comments ("0xFD - 공장출하시 설정 아이디").
FACTORY_DEFAULT_SERVO_ID = 0xFD

#: Legacy serial settings used by ``connect(nPort, nBaudRate)``: 8 data bits, no parity, 1 stop bit.
DEFAULT_BAUDRATE = 115200

#: One servo play-time tick in milliseconds (``CalcTime_ms``: "1 Tick 당 11.2 ms").
TICK_MS = 11.2
#: The play-time field is a single byte.
MAX_PLAYTIME_TICKS = 255

#: Size of a robot-side file name field (``_SIZE_FILENAME``), including the NUL terminator.
FILENAME_FIELD = 51
#: Size of each auth field (``nSize1``/``nSize2`` in ``request_auth``).
AUTH_FIELD = 21
#: File name field used by the TCP ``ActionPlay`` (``C_TCP_FILENAME_SIZE``).
TCP_FILENAME_FIELD = 16

#: Maximum value of a bus packet's one-byte size field.
MAX_BUS_PACKET = 255
#: Bytes before the payload in a bus packet (header x2, size, id, cmd, checksum x2).
BUS_HEADER_SIZE = 7


class Storage(IntEnum):
    """Robot-side file area (``nMaster_or_User``: 0 = master, 1 = user)."""

    MASTER = 0
    USER = 1


class LinkCommand(IntEnum):
    """Commands carried by Bluetooth link frames (``CGenibo_Bt.send_data``)."""

    FILE_LIST = 0x31  # request_filelist / reply parsed in Parser()
    FILE_SEND = 0x32  # request_file_download  (legacy name: PC -> robot)
    FILE_FETCH = 0x33  # request_file_upload    (legacy name: robot -> PC)
    FILE_DELETE = 0x34  # request_file_delete(storage, name)
    FILE_DELETE_ALL = 0x35  # request_file_delete(storage)
    PLAY = 0x40  # request_motion_play* / request_task_play
    IS_PLAYING = 0x41  # request_motion_is_playing / request_task_is_playing
    STOP = 0x42  # request_motion_stop / request_task_stop
    SET_TIME = 0x61  # request_set_time
    GET_TIME = 0x62  # request_time
    BATTERY = 0x63  # request_battery
    CHARGE = 0x64  # request_charge
    REMOCON = 0x65  # request_remocon
    AUTH = 0x71  # request_auth
    SERVO_BUS = 0x81  # Make_And_Send_Packet / serial_mpsu_* (tunnelled bus packet)
    STATUS = 0xF0  # robot -> host status report
    HEARTBEAT = 0xF1  # request_heartbeat


class StatusCode(IntEnum):
    """Status codes reported in ``STATUS`` (0xF0) replies (``_RET_*`` constants)."""

    RECEIVE_OK = 0
    PROCESS_END = 1
    PROCESS_FAILED = 2
    CHECKSUM_ERROR = 3
    TIME_OUT_ERROR = 4
    LENGTH_ERROR = 5


class BusCommand(IntEnum):
    """Commands inside servo-bus / MPSU packets."""

    # Servo commands used by CGenibo_Bt (HerkuleX-style).
    RAM_WRITE = 0x03  # drvsrv(): torque control register write
    S_JOG = 0x06  # request_move(): synchronised position/speed command
    REBOOT_LEGACY = 0x09  # only *named* in request_reset(); never actually transmitted there
    # MPSU (motion controller) commands.
    MPSU_MOTOR_MOVE = 0x0F  # drbluetooth_mpsu_motor_request_move
    MPSU_ROM_WRITE = 0x11  # serial_mpsu_write_rom
    MPSU_ROM_READ = 0x12  # reply command 0x52 (COjw_19_Herculex.cs: _CMD_MPSU_ROM2)
    MPSU_RAM_WRITE = 0x13  # serial_mpsu_write_ram
    MPSU_RAM_READ = 0x14  # drbluetooth_mpsu_request_battery (reply 0x54)
    MPSU_PLAY_MOTION = 0x16  # serial_mpsu_play_motion / drbluetooth_mpsu_request_motion_play
    MPSU_PLAY_TASK = 0x17  # serial_mpsu_play_task
    MPSU_HEADLED_BUZZ = 0x18  # serial_mpsu_play_headled_buzz / _headled / _buzz
    # Defined only in COjw_19_Herculex.cs (COjwMotor), not used by CGenibo_Bt.
    MPSU_REBOOT = 0x1B
    MPSU_REMOCON = 0x1D
    MPSU_SERVO_FW_UPDATE_MODE = 0x1E


#: Reply command codes for bus reads (``_CMD_*`` in CGenibo_Bt).
BUS_REPLY_EEP_READ = 0x42
BUS_REPLY_RAM_READ = 0x44
BUS_REPLY_HEAD_ROM = 0x4B
BUS_REPLY_HEAD_RAM = 0x4D
BUS_REPLY_MPSU_ROM = 0x52
BUS_REPLY_MPSU_RAM = 0x54
BUS_REPLY_OPSU_SENSOR = 0x74
BUS_REPLY_COMMANDS = frozenset(
    {
        BUS_REPLY_EEP_READ,
        BUS_REPLY_RAM_READ,
        BUS_REPLY_HEAD_ROM,
        BUS_REPLY_HEAD_RAM,
        BUS_REPLY_MPSU_ROM,
        BUS_REPLY_MPSU_RAM,
        BUS_REPLY_OPSU_SENSOR,
        BusCommand.MPSU_ROM_READ,
        BusCommand.MPSU_RAM_READ,
    }
)

#: MPSU controller IDs accepted by the legacy receive path (0x40..0x5F, or 253).
MPSU_ID_RANGE = range(0x40, 0x60)
MPSU_ALT_ID = 253

#: Motion index used by the legacy code to stop MPSU motion/task playback.
MPSU_STOP_INDEX = 0xFE


class JogFlag(IntFlag):
    """Per-servo SET byte of an S_JOG entry (``_FLAG_*``)."""

    NONE = 0x00
    STOP = 0x01
    SPEED_MODE = 0x02
    LED_GREEN = 0x04
    LED_BLUE = 0x08
    LED_RED = 0x10
    NO_ACTION = 0x20


class ServoRegister(IntEnum):
    """Servo RAM register addresses named in CGenibo_Bt (``_ADDRESS_*``)."""

    TORQUE_CONTROL = 52
    LED_CONTROL = 53
    VOLTAGE = 54
    TEMPERATURE = 55
    PRESENT_CONTROL_MODE = 56
    TICK = 57
    CALIBRATED_POSITION = 58


class Torque(IntFlag):
    """Bits written to ``TORQUE_CONTROL`` by ``drvsrv(bDriver, bServo)``."""

    FREE = 0x00
    SERVO = 0x20
    DRIVER = 0x40
    ON = 0x60


#: MPSU RAM address/length read by ``drbluetooth_mpsu_request_battery``.
MPSU_BATTERY_ADDRESS = 70
MPSU_BATTERY_LENGTH = 7


# --- TCP link (Ojw.CGenibo) ------------------------------------------------------------

#: First TCP port; channel *n* listens on ``TCP_BASE_PORT + n``.
TCP_BASE_PORT = 6100
#: Frame IDs on the TCP link are ``0x90 + channel``.
TCP_ID_BASE = 0x90


class TcpChannel(IntEnum):
    """TCP channels opened by ``CGenibo.Connect`` (one socket each)."""

    ROBOT = 0
    SOUND = 1
    VISION = 2
    MOTION = 3
    SENSOR = 4
    EMOTICON = 5


class TcpCommand(IntEnum):
    AUTH = 0x71  # sent on every channel
    ACTION_PLAY_MASTER = 0x33  # motion channel
    ACTION_PLAY_USER = 0x34  # motion channel
    STATUS = 0xF0
    HEARTBEAT = 0xF1


#: Named motor IDs from ``CGenibo`` (TCP class). Whether these equal the servo
#: bus IDs of a physical Genibo has NOT been verified.
CGENIBO_MOTOR_IDS: dict[str, int] = {
    "left_front_wing": 0,
    "left_front_down": 1,
    "left_front_up": 2,
    "right_front_wing": 3,
    "right_front_down": 4,
    "right_front_up": 5,
    "left_rear_wing": 6,
    "left_rear_down": 7,
    "left_rear_up": 8,
    "right_rear_wing": 9,
    "right_rear_down": 10,
    "right_rear_up": 11,
    "head_mouth": 14,
    "head_pan": 15,
    "head_tilt": 16,
    "tail": 17,
    "head_tilt_up": 18,
    "left_ear": 19,
    "right_ear": 20,
}
