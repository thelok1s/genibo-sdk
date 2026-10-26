"""Robot panel: connection (simulator or Bluetooth serial), status, files, E-STOP, plan streaming."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from genibo.errors import GeniboError
from genibo.joints import JointMap
from genibo.program.plan import CommandPlan
from genibo.program.player import play_plan
from genibo.program.safety import analyze_plan
from genibo.protocol.constants import Storage
from genibo.robot import Genibo
from genibo.studio.theme import PALETTE


class _Bridge(QObject):
    done = Signal(object, object, object)  # callback, result, error


class RobotPanel(QWidget):
    connection_changed = Signal(str, str)  # mode ("off"|"sim"|"hw"), description
    status_message = Signal(str)

    def __init__(self, joints_provider: Callable[[], JointMap], robot_id_provider: Callable[[], int]) -> None:
        super().__init__()
        self._joints = joints_provider
        self._robot_id = robot_id_provider
        self.robot: Genibo | None = None
        self._bridge = _Bridge()
        self._bridge.done.connect(self._finish)
        self._cancel: threading.Event | None = None

        # --- connection
        self.mode = QComboBox()
        self.mode.addItems(["Simulator", "Bluetooth serial"])
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(180)
        refresh = QPushButton("↻")
        refresh.setToolTip("Rescan serial ports")
        refresh.clicked.connect(self.refresh_ports)
        self.baud = QSpinBox()
        self.baud.setRange(1200, 3_000_000)
        self.baud.setValue(115200)
        self.robot_id_edit = QLineEdit()
        self.robot_id_edit.setPlaceholderText("from genibo.toml")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.badge = QLabel("DISCONNECTED")
        self.badge.setObjectName("badge")
        self._set_badge("off", "")
        port_row = QHBoxLayout()
        port_row.addWidget(self.port, 1)
        port_row.addWidget(refresh)
        form = QFormLayout()
        form.addRow("Link", self.mode)
        form.addRow("Port", port_row)
        form.addRow("Baud", self.baud)
        form.addRow("Robot ID", self.robot_id_edit)
        conn = QGroupBox("Connection")
        cl = QVBoxLayout(conn)
        cl.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(self.badge, 1)
        row.addWidget(self.connect_btn)
        cl.addLayout(row)

        # --- status / control
        self.status_lbl = QLabel("—")
        self.status_lbl.setWordWrap(True)
        b_status = QPushButton("Status")
        b_status.clicked.connect(self.refresh_status)
        b_time = QPushButton("Sync time")
        b_time.clicked.connect(lambda: self._robot_call(lambda r: r.set_time(), "robot clock set"))
        b_ton = QPushButton("Torque on")
        b_ton.clicked.connect(lambda: self._robot_call(lambda r: r.torque(True), "torque on sent"))
        b_toff = QPushButton("Torque free")
        b_toff.clicked.connect(lambda: self._robot_call(lambda r: r.torque(False), "torque free sent"))
        self.estop = QPushButton("E-STOP  (torque free + latch)")
        self.estop.setObjectName("estop")
        self.estop.clicked.connect(self.emergency_stop)
        b_clear = QPushButton("Clear latch")
        b_clear.clicked.connect(self._clear_latch)
        ctrl = QGroupBox("Robot")
        gl = QVBoxLayout(ctrl)
        gl.addWidget(self.status_lbl)
        r1 = QHBoxLayout()
        for b in (b_status, b_time, b_ton, b_toff):
            r1.addWidget(b)
        gl.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(self.estop, 1)
        r2.addWidget(b_clear)
        gl.addLayout(r2)

        # --- files
        self.storage = QComboBox()
        self.storage.addItems(["master", "user"])
        self.files = QListWidget()
        self.files.setMaximumHeight(140)
        fb = [
            ("List", self.refresh_files),
            ("Upload…", self.upload_file),
            ("Download…", self.download_file),
            ("Delete", self.delete_file),
            ("Play", self.play_file),
            ("Stop", lambda: self._robot_call(lambda r: r.stop_playback(), "stop sent")),
        ]
        files = QGroupBox("Files on robot (opaque .act/task files)")
        fl = QVBoxLayout(files)
        top = QHBoxLayout()
        top.addWidget(QLabel("Area"))
        top.addWidget(self.storage, 1)
        fl.addLayout(top)
        fl.addWidget(self.files)
        frow = QHBoxLayout()
        for text, fn in fb:
            b = QPushButton(text)
            b.clicked.connect(fn)
            frow.addWidget(b)
        fl.addLayout(frow)

        # --- plan
        self.allow_unverified = QCheckBox("Allow unverified joints on hardware")
        self.run_lbl = QLabel("Build a plan, then run it on the connected robot or simulator.")
        self.run_lbl.setWordWrap(True)
        self.run_lbl.setObjectName("muted")
        self.cancel_btn = QPushButton("Cancel run")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_run)
        run = QGroupBox("Run plan")
        rl = QVBoxLayout(run)
        rl.addWidget(self.allow_unverified)
        rl.addWidget(self.run_lbl)
        rl.addWidget(self.cancel_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        for g in (conn, ctrl, files, run):
            lay.addWidget(g)
        lay.addStretch(1)
        self.refresh_ports()

    # ------------------------------------------------------------------ helpers
    def _set_badge(self, mode: str, desc: str) -> None:
        text, color = {
            "off": ("DISCONNECTED", PALETTE["border"]),
            "sim": ("SIMULATOR", PALETTE["simulated"]),
            "hw": ("HARDWARE", PALETTE["hardware"]),
        }[mode]
        self.badge.setText(text)
        self.badge.setToolTip(desc)
        self.badge.setStyleSheet(f"background:{color};")

    def _async(self, fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None) -> None:
        def work() -> None:
            try:
                self._bridge.done.emit(on_done, fn(), None)
            except Exception as exc:
                self._bridge.done.emit(on_done, None, exc)

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, cb: Callable[[Any], None] | None, result: Any, error: BaseException | None) -> None:
        if error is not None:
            self.status_message.emit(f"error: {error}")
            QMessageBox.warning(self, "Robot", f"{type(error).__name__}: {error}")
            return
        if cb is not None:
            cb(result)

    def _need_robot(self) -> Genibo | None:
        if self.robot is None:
            QMessageBox.information(self, "Robot", "Connect to the simulator or a robot first.")
        return self.robot

    def _robot_call(self, fn: Callable[[Genibo], Any], ok_message: str) -> None:
        robot = self._need_robot()
        if robot is not None:
            self._async(lambda: fn(robot), lambda _r: self.status_message.emit(ok_message))

    def _storage(self) -> Storage:
        return Storage.MASTER if self.storage.currentIndex() == 0 else Storage.USER

    def current_robot_id(self) -> int:
        text = self.robot_id_edit.text().strip()
        return int(text, 0) if text else self._robot_id()

    # ------------------------------------------------------------------ connection
    def refresh_ports(self) -> None:
        from genibo.transport.serial import list_ports

        current = self.port.currentText()
        self.port.clear()
        try:
            for p in list_ports():
                self.port.addItem(p.device)
        except Exception as exc:  # pragma: no cover - platform specific
            self.status_message.emit(f"port scan failed: {exc}")
        if current:
            self.port.setEditText(current)

    def toggle_connection(self) -> None:
        if self.robot is not None:
            self.robot.close()
            self.robot = None
            self.connect_btn.setText("Connect")
            self._set_badge("off", "")
            self.connection_changed.emit("off", "")
            return
        try:
            rid = self.current_robot_id()
            if self.mode.currentIndex() == 0:
                from genibo.sim import VirtualGenibo

                dev = VirtualGenibo(robot_id=rid)
                dev.add_file(Storage.MASTER, "7.act", b"(opaque simulated motion)")
                robot = Genibo.simulator(dev, robot_id=rid, joints=self._joints())
                mode = "sim"
            else:
                port = self.port.currentText().strip()
                if not port:
                    QMessageBox.information(self, "Robot", "Choose the Genibo's serial (Bluetooth SPP) port.")
                    return
                robot = Genibo.serial(port, self.baud.value(), robot_id=rid, joints=self._joints())
                mode = "hw"
            robot.open()
        except (GeniboError, ValueError) as exc:
            QMessageBox.warning(self, "Connect", str(exc))
            return
        self.robot = robot
        desc = robot.link.transport.description
        self.connect_btn.setText("Disconnect")
        self._set_badge(mode, desc)
        self.connection_changed.emit(mode, desc)
        self.status_message.emit(f"connected: {desc}")

    # ------------------------------------------------------------------ actions
    def refresh_status(self) -> None:
        robot = self._need_robot()
        if robot is None:
            return

        def query() -> str:
            b = robot.battery()
            rtt = robot.heartbeat() * 1000
            playing = robot.is_playing()
            return (
                f"battery {b.robot_percent}% (MID {b.mid_percent}%) · heartbeat {rtt:.1f} ms · "
                f"{'playing' if playing else 'idle'}"
            )

        self._async(query, self.status_lbl.setText)

    def emergency_stop(self) -> None:
        if self.robot is None:
            return
        if self._cancel is not None:
            self._cancel.set()
        try:
            self.robot.emergency_stop()
            self.status_message.emit("E-STOP sent: torque free broadcast; motion latched")
        except GeniboError as exc:
            QMessageBox.critical(self, "E-STOP", f"could not send E-STOP: {exc}")

    def _clear_latch(self) -> None:
        if self.robot is not None:
            self.robot.clear_emergency()
            self.status_message.emit("E-STOP latch cleared (nothing sent)")

    def refresh_files(self) -> None:
        robot = self._need_robot()
        if robot is None:
            return
        storage = self._storage()

        def show(names: list[str]) -> None:
            self.files.clear()
            self.files.addItems(names or [])

        self._async(lambda: robot.list_files(storage), show)

    def upload_file(self) -> None:
        robot = self._need_robot()
        if robot is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "File to store on the robot (sent verbatim)")
        if not path:
            return
        storage = self._storage()
        name = Path(path).name

        def done(_result: object) -> None:
            self.status_message.emit(f"sent {name}")
            self.refresh_files()

        self._async(lambda: robot.upload_file(name, Path(path), storage), done)

    def _selected(self) -> str | None:
        it = self.files.currentItem()
        if it is None:
            QMessageBox.information(self, "Files", "Select a file first (List to refresh).")
            return None
        return it.text()

    def download_file(self) -> None:
        robot, name = self._need_robot(), self._selected()
        if robot is None or name is None:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save robot file", name)
        if not dest:
            return
        storage = self._storage()
        self._async(
            lambda: Path(dest).write_bytes(robot.download_file(name, storage)),
            lambda n: self.status_message.emit(f"saved {n} bytes to {dest}"),
        )

    def delete_file(self) -> None:
        robot, name = self._need_robot(), self._selected()
        if robot is None or name is None:
            return
        if (
            QMessageBox.question(self, "Delete", f"Delete {name!r} from the robot?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        storage = self._storage()
        self._async(lambda: robot.delete_file(name, storage), lambda _r: self.refresh_files())

    def play_file(self) -> None:
        robot, name = self._need_robot(), self._selected()
        if robot is not None and name is not None:
            storage = self._storage()
            self._async(
                lambda: robot.play_motion(name, storage), lambda _r: self.status_message.emit(f"play {name}")
            )

    # ------------------------------------------------------------------ plans
    def run_plan(self, plan: CommandPlan) -> None:
        robot = self._need_robot()
        if robot is None:
            return
        if plan.robot_id != robot.robot_id:
            QMessageBox.warning(
                self,
                "Run plan",
                f"Plan targets robot 0x{plan.robot_id:02x} but the link uses "
                f"0x{robot.robot_id:02x}. Rebuild or change the robot ID.",
            )
            return
        if robot.is_hardware:
            # Gate on what the frames do, judged against the project's own joint table.
            analysis = analyze_plan(plan)
            unverified = analysis.unverified_servos(self._joints().to_config())
            if unverified and not self.allow_unverified.isChecked():
                QMessageBox.warning(
                    self,
                    "Run plan",
                    "This plan moves servos that are not verified on hardware:\n"
                    + ", ".join(unverified)
                    + "\n\nVerify them in genibo.toml, or tick "
                    "'Allow unverified joints'.",
                )
                return
            hazards = ""
            if analysis.hazards:
                hazards = (
                    "\n\nThe plan ALSO changes robot state beyond motion:\n"
                    + "\n".join(analysis.hazards[:12])
                    + ("\n…" if len(analysis.hazards) > 12 else "")
                )
            msg = (
                f"Stream {len(plan.frames)} frames ({plan.duration_ms / 1000:.1f} s) to the PHYSICAL robot on "
                f"{robot.link.transport.description}?{hazards}\n\nKeep the robot clear. E-STOP sends torque-free."
            )
            if QMessageBox.question(self, "Run on hardware", msg) != QMessageBox.StandardButton.Yes:
                return
        self._cancel = threading.Event()
        cancel = self._cancel
        self.cancel_btn.setEnabled(True)
        self.run_lbl.setText(f"running {plan.name!r} ({len(plan.frames)} frames)…")

        def work() -> str:
            try:
                report = play_plan(plan, robot.link, cancel=cancel, robot_id=robot.robot_id)
            except GeniboError as exc:
                return f"failed: {exc}"
            state = "CANCELLED" if report.cancelled else "done"
            return f"{state}: sent {report.sent}/{report.total} frames, worst lateness {report.max_lateness_ms:.1f} ms"

        def finish(text: str) -> None:
            self.run_lbl.setText(text)
            self.cancel_btn.setEnabled(False)
            self.status_message.emit(text)

        self._async(work, finish)

    def cancel_run(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
