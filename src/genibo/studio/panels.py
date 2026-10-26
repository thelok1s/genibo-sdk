"""Dock panels: problems, plan inspector, simulator, log, API reference."""

from __future__ import annotations

import html
import inspect
import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from genibo.errors import Diagnostic
from genibo.joints import JointMap
from genibo.program.plan import CommandPlan
from genibo.protocol.describe import describe
from genibo.sim.device import ASSUMPTIONS
from genibo.sim.trajectory import Trajectory, simulate_plan
from genibo.studio.editor import mono_font
from genibo.studio.theme import PALETTE, SERIES


def _item(text: str, color: str | None = None, align_right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if color:
        it.setForeground(QColor(color))
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.horizontalHeader().setStretchLastSection(True)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    return t


# ---------------------------------------------------------------------------- problems


class ProblemsPanel(QWidget):
    line_activated = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.table = _table(["", "Code", "Line", "Message"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(self._activate)
        self.table.cellClicked.connect(self._activate)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        self._lines: list[int] = []

    def set_diagnostics(self, diags: list[Diagnostic]) -> None:
        order = {"error": 0, "warning": 1, "info": 2}
        diags = sorted(diags, key=lambda d: (order.get(d.severity, 3), d.location.line if d.location else 0))
        self.table.setRowCount(len(diags))
        self._lines = []
        for r, d in enumerate(diags):
            color = PALETTE.get(d.severity, PALETTE["text"])
            self.table.setItem(r, 0, _item("●", color))
            self.table.setItem(r, 1, _item(d.code, color))
            line = d.location.line if d.location else 0
            self._lines.append(line)
            self.table.setItem(r, 2, _item(str(line) if line else "", align_right=True))
            msg = d.message + (f"  — {d.hint}" if d.hint else "")
            self.table.setItem(r, 3, _item(msg))

    def _activate(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._lines) and self._lines[row]:
            self.line_activated.emit(self._lines[row])


# ---------------------------------------------------------------------------- plan inspector


class PlanPanel(QWidget):
    line_activated = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.header = QLabel("No plan built yet. Press Build (Ctrl+B).")
        self.header.setObjectName("muted")
        self.table = _table(["t (ms)", "Line", "Step  <frame>"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(mono_font(11))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([620, 420])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.header)
        lay.addWidget(split, 1)
        self.plan: CommandPlan | None = None
        self.table.currentCellChanged.connect(lambda r, *_: self._show(r))
        self.table.cellDoubleClicked.connect(self._jump)

    def set_plan(self, plan: CommandPlan | None) -> None:
        self.plan = plan
        if plan is None:
            self.header.setText("No plan (build failed or not built).")
            self.table.setRowCount(0)
            self.detail.clear()
            return
        verified = "verified" if not plan.verify() else "VERIFY FAILED"
        self.header.setText(
            f"{plan.name}: {len(plan.frames)} frames · {plan.duration_ms:.1f} ms · robot 0x{plan.robot_id:02x} · "
            f"frames sha256 {plan.frames_digest()[:16]} · {verified} · joints {plan.joints.get('profile', '?')}"
        )
        self.table.setRowCount(len(plan.frames))
        for r, f in enumerate(plan.frames):
            self.table.setItem(r, 0, _item(f"{f.t_ms:.1f}", align_right=True))
            self.table.setItem(r, 1, _item(str(f.line or ""), align_right=True))
            self.table.setItem(r, 2, _item(f.summary))
        if plan.frames:
            self.table.selectRow(0)

    def _show(self, row: int) -> None:
        if self.plan is None or not 0 <= row < len(self.plan.frames):
            return
        f = self.plan.frames[row]
        text = [f"t = {f.t_ms:.1f} ms   step {f.step}   source line {f.line}", "", "bytes:"]
        hexs = f.data.hex(" ")
        text += ["  " + hexs[i : i + 48] for i in range(0, len(hexs), 48)]
        text += ["", *describe(f.decode()).lines()]
        self.detail.setPlainText("\n".join(text))

    def _jump(self, row: int, _col: int) -> None:
        if self.plan is not None and 0 <= row < len(self.plan.frames) and self.plan.frames[row].line:
            self.line_activated.emit(int(self.plan.frames[row].line or 0))


# ---------------------------------------------------------------------------- simulator


class JointPlot(QWidget):
    """Joint angle (or raw) curves over plan time, with a time cursor."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(180)
        self.series: list[tuple[str, list[float], list[float]]] = []  # name, times, values
        self.cursor_ms = 0.0
        self.unit = "deg"
        self.frame_times: list[float] = []

    def set_series(
        self, series: list[tuple[str, list[float], list[float]]], unit: str, frame_times: list[float]
    ) -> None:
        self.series, self.unit, self.frame_times = series, unit, frame_times
        self.update()

    def paintEvent(self, _e) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(46, 12, -12, -26)
        p.fillRect(self.rect(), QColor(PALETTE["panel"]))
        if not self.series:
            p.setPen(QColor(PALETTE["muted"]))
            p.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Simulate a built plan to see joint trajectories"
            )
            return
        t_end = max(ts[-1] for _, ts, _ in self.series) or 1.0
        lo = min(min(v) for _, _, v in self.series)
        hi = max(max(v) for _, _, v in self.series)
        if hi - lo < 1e-6:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad

        def x(t: float) -> float:
            return r.left() + r.width() * t / t_end

        def y(v: float) -> float:
            return r.bottom() - r.height() * (v - lo) / (hi - lo)

        grid = QPen(QColor(PALETTE["border"]))
        p.setPen(grid)
        for i in range(5):
            gy = r.top() + r.height() * i / 4
            p.drawLine(QPointF(r.left(), gy), QPointF(r.right(), gy))
            p.setPen(QColor(PALETTE["muted"]))
            p.drawText(
                QRectF(0, gy - 8, 42, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{round(hi - (hi - lo) * i / 4) + 0:d}",  # "+ 0" turns -0 into 0
            )
            p.setPen(grid)
        # frame send times as ticks on the axis
        p.setPen(QPen(QColor(PALETTE["muted"]), 1))
        for ft in self.frame_times:
            p.drawLine(QPointF(x(ft), r.bottom()), QPointF(x(ft), r.bottom() + 4))
        p.drawText(
            QRectF(r.left(), r.bottom() + 6, r.width(), 16),
            Qt.AlignmentFlag.AlignRight,
            f"{t_end:.0f} ms   ({self.unit})",
        )
        for i, (name, ts, vs) in enumerate(self.series):
            path = QPainterPath(QPointF(x(ts[0]), y(vs[0])))
            for t, v in zip(ts[1:], vs[1:], strict=True):
                path.lineTo(QPointF(x(t), y(v)))
            p.setPen(QPen(QColor(SERIES[i % len(SERIES)]), 1.8))
            p.drawPath(path)
            p.drawText(QPointF(r.left() + 6 + 120 * (i % 6), r.top() + 12 + 14 * (i // 6)), name)
        p.setPen(QPen(QColor(PALETTE["text"]), 1, Qt.PenStyle.DashLine))
        cx = x(min(self.cursor_ms, t_end))
        p.drawLine(QPointF(cx, r.top()), QPointF(cx, r.bottom()))


class SimPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        banner = QLabel("SIMULATOR — protocol-level stand-in; behaviour is inferred, not hardware evidence")
        banner.setObjectName("badge")
        banner.setStyleSheet(f"background:{PALETTE['simulated']};")
        self.plot = JointPlot()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.time_label = QLabel("0.0 ms")
        self.time_label.setMinimumWidth(90)
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setEnabled(False)
        self.joints_table = _table(["Joint", "ID", "Raw", "Angle"])
        self.events = QListWidget()
        self.events.setFont(mono_font(11))
        assumptions = QLabel("Assumptions: " + " ".join(ASSUMPTIONS))
        assumptions.setWordWrap(True)
        assumptions.setObjectName("muted")
        ctl = QHBoxLayout()
        ctl.addWidget(self.play_btn)
        ctl.addWidget(self.slider, 1)
        ctl.addWidget(self.time_label)
        lower = QSplitter(Qt.Orientation.Horizontal)
        lower.addWidget(self.joints_table)
        lower.addWidget(self.events)
        lower.setSizes([380, 520])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(banner)
        lay.addWidget(self.plot, 2)
        lay.addLayout(ctl)
        lay.addWidget(lower, 2)
        lay.addWidget(assumptions)
        self.traj: Trajectory | None = None
        self.joints: JointMap | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.slider.valueChanged.connect(self._scrub)
        self.play_btn.clicked.connect(self._toggle)

    def simulate(self, plan: CommandPlan, joints: JointMap, robot_files: list[tuple[int, str]]) -> Trajectory:
        traj = simulate_plan(plan, robot_files=robot_files, tail_ms=200.0)
        self.traj, self.joints = traj, joints
        moving = traj.moving_servos()
        series = []
        unit = "deg"
        for sid in moving:
            j = joints.by_servo_id(sid)
            if j is not None and j.calibration is not None:
                vals = [j.to_angle(round(v)) for v in traj.raw[sid]]
                series.append((j.name, traj.times_ms, vals))
            else:
                unit = "raw/deg"
                series.append((f"servo{sid}", traj.times_ms, traj.raw[sid]))
        self.plot.set_series(series, unit, traj.frame_times_ms)
        self.events.clear()
        for e in traj.events:
            self.events.addItem(f"{e.t * 1000:9.1f} ms  {e.kind:8} {e.text}")
        self.play_btn.setEnabled(True)
        self.slider.setValue(0)
        self._scrub(0)
        return traj

    def _t(self) -> float:
        if self.traj is None or not self.traj.times_ms:
            return 0.0
        return self.traj.times_ms[-1] * self.slider.value() / 1000.0

    def _scrub(self, _v: int) -> None:
        if self.traj is None or self.joints is None:
            return
        t = self._t()
        self.plot.cursor_ms = t
        self.plot.update()
        self.time_label.setText(f"{t:.1f} ms")
        state = self.traj.at(t)
        moving = set(self.traj.moving_servos())
        rows = [(j, state.get(j.servo_id, 0.0)) for j in self.joints.values() if j.servo_id in state]
        rows.sort(key=lambda jr: (jr[0].servo_id not in moving, jr[0].servo_id))
        self.joints_table.setRowCount(len(rows))
        for r, (j, raw) in enumerate(rows):
            color = PALETTE["text"] if j.servo_id in moving else PALETTE["muted"]
            self.joints_table.setItem(r, 0, _item(j.name, color))
            self.joints_table.setItem(r, 1, _item(str(j.servo_id), color, True))
            self.joints_table.setItem(r, 2, _item(f"{raw:.1f}", color, True))
            ang = f"{j.to_angle(round(raw)):.2f}°" if j.calibration else "—"
            self.joints_table.setItem(r, 3, _item(ang, color, True))

    def _toggle(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.play_btn.setText("▶ Play")
        else:
            if self.slider.value() >= 1000:
                self.slider.setValue(0)
            self.timer.start()
            self.play_btn.setText("❚❚ Pause")

    def _tick(self) -> None:
        if self.traj is None:
            return
        total = self.traj.times_ms[-1] or 1.0
        step = 1000.0 * 33.0 / total
        nv = self.slider.value() + max(1, round(step))
        if nv >= 1000:
            self.slider.setValue(1000)
            self._toggle()
        else:
            self.slider.setValue(nv)


# ---------------------------------------------------------------------------- log


class _Bridge(QObject):
    message = Signal(str, str)


class QtLogHandler(logging.Handler):
    """Forwards ``genibo`` log records to the UI thread."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.bridge = _Bridge()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = f"{record.levelname.lower():7} {record.name}: {record.getMessage()}"
            hexs = getattr(record, "hex", None)
            if hexs:
                text += f"\n{'':8}{hexs}"
            self.bridge.message.emit(record.levelname, text)
        except Exception:
            self.handleError(record)


class LogPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(mono_font(11))
        self.view.setMaximumBlockCount(5000)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.view.clear)
        top = QHBoxLayout()
        hint = QLabel("Structured log of the genibo logger · TX/RX frames appear here while connected")
        hint.setObjectName("muted")
        top.addWidget(hint, 1)
        top.addWidget(clear)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.view)
        self.handler = QtLogHandler()
        self.handler.bridge.message.connect(self.append)

    def append(self, level: str, text: str) -> None:
        color = {"ERROR": PALETTE["error"], "WARNING": PALETTE["warning"], "DEBUG": PALETTE["muted"]}.get(
            level, PALETTE["text"]
        )
        self.view.appendHtml(
            f'<span style="color:{color}">{html.escape(text).replace(chr(10), "<br>")}</span>'
        )


# ---------------------------------------------------------------------------- docs


def _api_section(title: str, cls: type, names: list[str] | None = None) -> str:
    out = [f"<h2>{html.escape(title)}</h2>"]
    doc = inspect.getdoc(cls)
    if doc:
        out.append(f"<p>{html.escape(doc.split(chr(10) + chr(10))[0])}</p>")
    members = names or [n for n in vars(cls) if not n.startswith("_")]
    for name in members:
        member = getattr(cls, name, None)
        if member is None or not callable(member):
            continue
        try:
            sig = str(inspect.signature(member)).replace("(self, ", "(").replace("(self)", "()")
        except (TypeError, ValueError):
            sig = "(...)"
        d = inspect.getdoc(member) or ""
        out.append(
            f"<p><code style='color:{PALETTE['step']}'>{html.escape(name)}{html.escape(sig)}</code><br>"
            f"<span style='color:{PALETTE['muted']}'>{html.escape(d).replace(chr(10), '<br>')}</span></p>"
        )
    return "\n".join(out)


PROGRAMMING_MODEL_HTML = f"""
<h1>Genibo SDK reference</h1>
<p><b>Two ways to drive the robot.</b> A <i>compiled program</i> (<code>@program def main(g)</code>) runs once on
your PC at build time and becomes a verified, timed list of frames (a command plan). A <i>host script</i>
(<code>def main(robot)</code>) runs live and can react to replies. The robot itself never runs Python: it receives
the same frames the legacy OpenJigWare tool sent, and plays motion files (<code>.act</code>) stored on it.</p>
<p style='color:{PALETTE["warning"]}'>Joint IDs and calibration in the default profile are not verified on
hardware. Confirm each joint with small moves before trusting angles.</p>
"""


class DocsPanel(QTextBrowser):
    def __init__(self, sections: list[Callable[[], str]]) -> None:
        super().__init__()
        self.setOpenExternalLinks(True)
        self.setHtml(PROGRAMMING_MODEL_HTML + "".join(s() for s in sections))
