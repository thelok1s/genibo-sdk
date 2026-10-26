"""Migration assistant: find OpenJigWare Genibo calls in C# sources and map them to the SDK.

This is a text-level scanner, not a C# compiler. It reports every recognised
call with its line, the equivalent :class:`genibo.Genibo` call, and caveats;
calls it does not recognise are listed so nothing is silently skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Mapping:
    legacy: str
    modern: str
    shim: str
    note: str = ""


# legacy method name -> (modern API, legacy-shim call, note)
GENIBO_BT: dict[str, Mapping] = {
    m.legacy: m
    for m in [
        Mapping(
            "connect",
            "Genibo.serial(port, baudrate).open()",
            "CGeniboBt().connect(port, baud)",
            "legacy int port N meant COM<N>; pass a device path on macOS/Linux",
        ),
        Mapping("disconnect", "robot.close()", "bt.disconnect()"),
        Mapping("connected", "robot.link.is_open", "bt.connected()"),
        Mapping("drbluetooth_set_id", "Genibo(..., robot_id=ID)", "bt.drbluetooth_set_id(ID)"),
        Mapping(
            "Cmd_Bluetooth_Play",
            "robot.play_motion(name, Storage(master))",
            "bt.Cmd_Bluetooth_Play(id, m, name)",
            "sets the robot ID as a side effect in the legacy API",
        ),
        Mapping(
            "Cmd_Bluetooth_Play_Mpsu",
            "robot.mpsu_play_motion(index, mpsu_id=ID, direct=True)",
            "bt.Cmd_Bluetooth_Play_Mpsu(id, index)",
        ),
        Mapping(
            "request_motion_play_now",
            "robot.play_motion(name, storage)",
            "bt.request_motion_play_now(m, name)",
        ),
        Mapping(
            "request_motion_play",
            "robot.play_motion(name, storage, at=datetime, add_ms=n)",
            "bt.request_motion_play(m, name, dt, ms)",
        ),
        Mapping("Action_Play", "robot.play_motion(name, storage)", "bt.Action_Play(m, name)"),
        Mapping("request_task_play", "robot.play_task(name, storage)", "bt.request_task_play(m, name)"),
        Mapping("request_motion_stop", "robot.stop_playback()", "bt.request_motion_stop()"),
        Mapping("request_task_stop", "robot.stop_playback()", "bt.request_task_stop()"),
        Mapping(
            "request_motion_is_playing",
            "robot.is_playing()",
            "bt.request_motion_is_playing(); bt.wait(); bt.get_is_playing()",
            "the modern call waits for the reply and returns it",
        ),
        Mapping("request_task_is_playing", "robot.is_playing()", "bt.request_task_is_playing()"),
        Mapping("get_is_playing", "robot.is_playing()", "bt.get_is_playing()"),
        Mapping("request_filelist", "robot.list_files(storage)", "bt.request_filelist(m)"),
        Mapping("get_FileList_Count", "len(robot.list_files(storage))", "bt.get_FileList_Count(m)"),
        Mapping("get_FileList", "robot.list_files(storage)[i]", "bt.get_FileList(m, i)"),
        Mapping(
            "request_file_download",
            "robot.upload_file(name, Path(local), storage)",
            "bt.request_file_download(...)",
            "legacy 'download' = PC -> robot; the SDK calls this upload",
        ),
        Mapping(
            "request_file_upload",
            "robot.download_file(name, storage)",
            "bt.request_file_upload(m, name)",
            "legacy 'upload' = robot -> PC; the SDK returns the bytes instead of writing motion\\master\\...",
        ),
        Mapping(
            "request_file_delete",
            "robot.delete_file(name, storage) / robot.delete_all_files(storage, confirm=True)",
            "bt.request_file_delete(m[, name])",
        ),
        Mapping("request_set_time", "robot.set_time(datetime)", "bt.request_set_time(dt)"),
        Mapping("request_time", "robot.get_time()", "bt.request_time(); ...; bt.get_time_sec()"),
        Mapping("get_time_sec", "robot.get_time().timestamp()", "bt.get_time_sec()"),
        Mapping("get_time_usec", "robot.get_time().microsecond", "bt.get_time_usec()"),
        Mapping(
            "request_battery", "robot.battery()", "bt.request_battery(); bt.wait(); bt.get_battery_robot()"
        ),
        Mapping("get_battery_robot", "robot.battery().robot_percent", "bt.get_battery_robot()"),
        Mapping("get_battery_MID", "robot.battery().mid_percent", "bt.get_battery_MID()"),
        Mapping("request_charge", "robot.charge()", "bt.request_charge()"),
        Mapping("request_remocon", "robot.remocon()", "bt.request_remocon()"),
        Mapping("get_remocon_length", "robot.remocon().length_ticks", "bt.get_remocon_length()"),
        Mapping("get_remocon_data", "robot.remocon().data", "bt.get_remocon_data()"),
        Mapping("request_heartbeat", "robot.heartbeat()", "bt.request_heartbeat()"),
        Mapping("request_auth", "robot.auth(user, password)", "bt.request_auth(user, pw)"),
        Mapping(
            "set_param",
            "genibo.toml [joints.<name>] servo_id/center/mech_move/degree/direction/limit_*",
            "bt.set_param(axis, id, dir, up, dn, center, mech, deg)",
            "name your joints instead of using axis numbers",
        ),
        Mapping(
            "set_cmd_angle",
            "robot.move({joint: angle}, ms)",
            "bt.set_cmd_angle(axis, angle)",
            "out-of-limit angles raise unless clip=True (legacy clipped silently)",
        ),
        Mapping("set_cmd", "robot.move({joint: raw}, ms, raw=True)", "bt.set_cmd(axis, raw)"),
        Mapping(
            "request_move",
            "robot.move(...) / program g.pose(...)",
            "bt.request_move(ms) / bt.request_move(axis, ms)",
            "play time is limited to 255 ticks (~2856 ms); legacy wrapped silently",
        ),
        Mapping(
            "set_cmd_flag_led",
            "robot.move(..., led=Led.GREEN | Led.RED)",
            "bt.set_cmd_flag_led(axis, g, b, r)",
        ),
        Mapping(
            "set_cmd_flag_mode",
            "robot.jog([JogEntry(value, JogFlag.SPEED_MODE, id)], ms)",
            "bt.set_cmd_flag_mode(axis, on)",
            "speed-mode semantics on the robot are unverified",
        ),
        Mapping("drvsrv", "robot.torque(on[, joint]) / robot.torque_mode(Torque.X)", "bt.drvsrv(drv, srv)"),
        Mapping(
            "request_stop",
            "robot.stop_joints([...]) / robot.emergency_stop()",
            "bt.request_stop()",
            "legacy request_stop() with no axis never transmitted anything",
        ),
        Mapping(
            "request_ems", "robot.emergency_stop()", "bt.request_ems()", "sends the torque-free broadcast"
        ),
        Mapping("reset", "robot.clear_emergency()", "bt.reset()", "legacy reset() also sent an empty S_JOG"),
        Mapping("reset_stop", "robot.clear_emergency()", "bt.reset_stop()"),
        Mapping("reset_ems", "robot.clear_emergency()", "bt.reset_ems()"),
        Mapping(
            "serial_mpsu_play_headled_buzz",
            "robot.head_led_buzz(led, buzz, mpsu_id=ID)",
            "bt.serial_mpsu_play_headled_buzz(...)",
        ),
        Mapping(
            "serial_mpsu_play_headled",
            "robot.head_led_buzz(led=n, mpsu_id=ID)",
            "bt.serial_mpsu_play_headled(...)",
        ),
        Mapping(
            "serial_mpsu_play_buzz",
            "robot.head_led_buzz(buzz=n, mpsu_id=ID)",
            "bt.serial_mpsu_play_buzz(...)",
        ),
        Mapping(
            "serial_mpsu_play_motion",
            "robot.mpsu_play_motion(index, ready=.., mpsu_id=ID)",
            "bt.serial_mpsu_play_motion(...)",
        ),
        Mapping(
            "serial_mpsu_play_task", "robot.mpsu_play_task(task, mpsu_id=ID)", "bt.serial_mpsu_play_task(...)"
        ),
        Mapping(
            "serial_mpsu_write_rom",
            "robot.mpsu_write(addr, data, rom=True, mpsu_id=ID)",
            "bt.serial_mpsu_write_rom(...)",
            "ROM writes persist on the controller",
        ),
        Mapping(
            "serial_mpsu_write_ram",
            "robot.mpsu_write(addr, data, rom=False, mpsu_id=ID)",
            "bt.serial_mpsu_write_ram(...)",
        ),
        Mapping(
            "drbluetooth_mpsu_request_battery",
            "robot.mpsu_read_ram(70, 7, mpsu_id=ID)",
            "bt.drbluetooth_mpsu_request_battery()",
        ),
        Mapping(
            "drbluetooth_mpsu_request_motion_play",
            "robot.mpsu_play_motion(i, ready=r, mpsu_id=ID, direct=True)",
            "bt.drbluetooth_mpsu_request_motion_play(i, r)",
        ),
        Mapping(
            "drbluetooth_mpsu_request_motion_stop",
            "robot.mpsu_stop_motion(mpsu_id=ID, direct=True)",
            "bt.drbluetooth_mpsu_request_motion_stop()",
        ),
        Mapping("send_data", "robot.send_raw(cmd, payload)", "bt.send_data(cmd, n, data)"),
        Mapping("send_command", "robot.send_raw(cmd)", "bt.send_command(cmd)"),
        Mapping("wait", "(not needed: queries wait for their reply)", "bt.wait(ms)"),
        Mapping("packet_status", "RobotStatusError is raised on failure", "bt.packet_status()"),
    ]
}

GENIBO_TCP: dict[str, Mapping] = {
    m.legacy: m
    for m in [
        Mapping("Connect", "GeniboNet(host).open()", "-", "opens the six channels on ports 6100..6105"),
        Mapping("DisConnect", "net.close()", "-"),
        Mapping("Auth", "net.auth(user, password)", "-"),
        Mapping(
            "ActionPlay",
            "net.action_play(name, master=.., sound=.., emoticon=.., speed_percent=..)",
            "-",
            "file name limited to 15 bytes",
        ),
        Mapping("SendData", "net.send(channel, cmd, payload)", "-"),
    ]
}

MOTOR_MPSU: dict[str, Mapping] = {
    m.legacy: m
    for m in [
        Mapping(
            "Connect",
            "Genibo.serial(port, baudrate).open()",
            "-",
            "COjwMotor opened the MPSU serial line directly; use direct=True for MPSU calls",
        ),
        Mapping("DisConnect", "robot.close()", "-"),
        Mapping(
            "Mpsu_Play_Motion",
            "robot.mpsu_play_motion(index, ready=.., mpsu_id=ID, direct=True)",
            "-",
            "COjwMotor talks to the MPSU over a plain serial line; same packet as the direct Bluetooth form",
        ),
        Mapping("Mpsu_Stop_Motion", "robot.mpsu_stop_motion(direct=True)", "-"),
        Mapping("Mpsu_Play_Task", "robot.mpsu_play_task(task, direct=True)", "-"),
        Mapping("Mpsu_Play_HeadLed_Buzz", "robot.head_led_buzz(led, buzz)", "-"),
        Mapping("Mpsu_Write_Rom", "robot.mpsu_write(addr, data, rom=True, mpsu_id=ID)", "-"),
        Mapping("Mpsu_Write_Ram", "robot.mpsu_write(addr, data, rom=False, mpsu_id=ID)", "-"),
        Mapping(
            "DrvSrv",
            "robot.send(commands.torque(0xFE, Torque.ON))",
            "-",
            "COjwMotor sends the servo packet bare; robot.torque() tunnels it in link command 0x81 instead",
        ),
    ]
}

_DECL = re.compile(r"Ojw\.(CGenibo_Bt|CGenibo|COjwMotor)\s+(\w+)\s*=")
_CALL = re.compile(r"\b(\w+)\s*\.\s*(\w+)\s*\(")


@dataclass
class Finding:
    line: int
    variable: str
    legacy_class: str
    method: str
    code: str
    mapping: Mapping | None


@dataclass
class MigrationReport:
    path: str
    variables: dict[str, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @property
    def unmapped(self) -> list[Finding]:
        return [f for f in self.findings if f.mapping is None]

    def render(self) -> str:
        out = [f"Migration report for {self.path}"]
        if not self.variables:
            out.append("  no OpenJigWare Genibo objects found")
            return "\n".join(out) + "\n"
        out.append("  objects: " + ", ".join(f"{v} ({c})" for v, c in self.variables.items()))
        for f in self.findings:
            out.append(f"\n  line {f.line}: {f.code.strip()}")
            if f.mapping is None:
                out.append(f"    !! {f.legacy_class}.{f.method} has no mapping yet - port it manually")
                continue
            out.append(f"    modern : {f.mapping.modern}")
            if f.mapping.shim != "-":
                out.append(f"    shim   : {f.mapping.shim}   (genibo.legacy.CGeniboBt, byte-compatible)")
            if f.mapping.note:
                out.append(f"    note   : {f.mapping.note}")
        out.append(f"\n  {len(self.findings)} call(s), {len(self.unmapped)} without mapping")
        return "\n".join(out) + "\n"

    def skeleton(self) -> str:
        """A host-script skeleton with one commented line per legacy call."""
        lines = [
            f'"""Ported from {Path(self.path).name} by `genibo migrate`. Review every line."""',
            "",
            "from genibo import Genibo, Storage",
            "",
            "",
            "def main(robot: Genibo) -> None:",
        ]
        body = []
        for f in self.findings:
            body.append(f"    # legacy line {f.line}: {f.code.strip()}")
            if f.mapping is not None and f.method not in (
                "connect",
                "disconnect",
                "connected",
                "Connect",
                "DisConnect",
            ):
                body.append(f"    # TODO: {f.mapping.modern}")
        lines += body or ["    pass"]
        if body:
            lines.append("    pass")
        return "\n".join(lines) + "\n"


def scan(path: Path) -> MigrationReport:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    report = MigrationReport(str(path))
    for m in _DECL.finditer(text):
        report.variables[m.group(2)] = m.group(1)
    tables = {"CGenibo_Bt": GENIBO_BT, "CGenibo": GENIBO_TCP, "COjwMotor": MOTOR_MPSU}
    for lineno, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0]
        for m in _CALL.finditer(code):
            var, method = m.group(1), m.group(2)
            cls = report.variables.get(var)
            if cls is None:
                continue
            table = tables[cls]
            mapping = table.get(method)
            if cls == "COjwMotor" and mapping is None and not method.startswith("Mpsu"):
                continue  # generic servo-driver calls are outside the Genibo scope
            report.findings.append(Finding(lineno, var, cls, method, line, mapping))
    return report
