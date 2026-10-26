"""Genibo Studio main window."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from PySide6.QtCore import QDir, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from genibo import Genibo
from genibo.errors import Diagnostic, GeniboError, SourceLocation
from genibo.joints import JointMap, load_profile
from genibo.program.builder import Program
from genibo.program.compiler import BuildResult, compile_source
from genibo.program.plan import CommandPlan
from genibo.program.static_check import check_source
from genibo.project import Project, ProjectError, create_project
from genibo.studio.editor import CodeEditor, mono_font
from genibo.studio.panels import DocsPanel, LogPanel, PlanPanel, ProblemsPanel, SimPanel, _api_section
from genibo.studio.robot_panel import RobotPanel
from genibo.studio.theme import PALETTE

log = logging.getLogger("genibo.studio")

_TRUST_KEY = "trusted_projects"


def _settings() -> QSettings:
    return QSettings("genibo", "Genibo Studio")


def trusted_project_roots() -> set[str]:
    value = _settings().value(_TRUST_KEY, [])
    if isinstance(value, str):  # QSettings returns a bare string for one-element lists on some backends
        return {value}
    if isinstance(value, (list, tuple)):
        return {str(v) for v in value}
    return set()


def remember_trusted_project(root: Path) -> None:
    roots = trusted_project_roots() | {str(root.resolve())}
    _settings().setValue(_TRUST_KEY, sorted(roots))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Genibo Studio")
        self.resize(1480, 940)
        self.project: Project | None = None
        self.plan: CommandPlan | None = None
        self.last_build: BuildResult | None = None
        self.editors: dict[Path, CodeEditor] = {}
        # Projects whose program code may run on this machine (validation/build execute it).
        self._trusted: set[str] = set()

        # central editor tabs
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(lambda _i: self._schedule_validate())
        self.setCentralWidget(self.tabs)

        # panels
        self.problems = ProblemsPanel()
        self.plan_panel = PlanPanel()
        self.sim_panel = SimPanel()
        self.log_panel = LogPanel()
        self.docs = DocsPanel(
            [
                lambda: _api_section("Compiled program steps (g.*)", Program),
                lambda: _api_section(
                    "Live robot client (Genibo)",
                    Genibo,
                    [
                        "battery",
                        "heartbeat",
                        "get_time",
                        "set_time",
                        "is_playing",
                        "play_motion",
                        "play_task",
                        "stop_playback",
                        "list_files",
                        "upload_file",
                        "download_file",
                        "delete_file",
                        "move",
                        "jog",
                        "torque",
                        "torque_mode",
                        "stop_joints",
                        "emergency_stop",
                        "clear_emergency",
                        "head_led_buzz",
                        "mpsu_play_motion",
                        "mpsu_stop_motion",
                        "mpsu_read_ram",
                        "send_raw",
                    ],
                ),
            ]
        )
        self.robot_panel = RobotPanel(self.joint_map, self.robot_id)
        self.fs_model = QFileSystemModel()
        self.fs_model.setNameFilters(["*.py", "*.toml", "*.json", "*.lst", "*.md", "*.act"])
        self.fs_model.setNameFilterDisables(False)
        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        for col in (1, 2, 3):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self._tree_open)

        self._dock("Project", self.tree, Qt.DockWidgetArea.LeftDockWidgetArea)
        self._dock("Robot", self.robot_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        bottom = [
            self._dock("Problems", self.problems, Qt.DockWidgetArea.BottomDockWidgetArea),
            self._dock("Plan", self.plan_panel, Qt.DockWidgetArea.BottomDockWidgetArea),
            self._dock("Simulator", self.sim_panel, Qt.DockWidgetArea.BottomDockWidgetArea),
            self._dock("Log", self.log_panel, Qt.DockWidgetArea.BottomDockWidgetArea),
            self._dock("Reference", self.docs, Qt.DockWidgetArea.BottomDockWidgetArea),
        ]
        for d in bottom[1:]:
            self.tabifyDockWidget(bottom[0], d)
        bottom[0].raise_()
        self.bottom_docks = {d.windowTitle(): d for d in bottom}
        self.resizeDocks(bottom[:1], [380], Qt.Orientation.Vertical)

        # wiring
        self.problems.line_activated.connect(self._goto_entry_line)
        self.plan_panel.line_activated.connect(self._goto_entry_line)
        self.robot_panel.status_message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.robot_panel.connection_changed.connect(self._on_connection)
        logging.getLogger("genibo").addHandler(self.log_panel.handler)
        logging.getLogger("genibo").setLevel(logging.DEBUG)

        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(700)
        self._validate_timer.timeout.connect(self.validate)

        self._build_actions()
        self.project_lbl = QLabel("no project")
        self.build_lbl = QLabel("")
        self.link_lbl = QLabel("link: disconnected")
        for w in (self.project_lbl, self.build_lbl, self.link_lbl):
            w.setObjectName("muted")
            self.statusBar().addPermanentWidget(w)
        self._show_welcome()

    # ------------------------------------------------------------------ layout helpers
    def _dock(self, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        d.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(area, d)
        return d

    def _action(
        self,
        text: str,
        slot,
        shortcut: str | QKeySequence | QKeySequence.StandardKey | None = None,
        tip: str = "",
    ) -> QAction:  # type: ignore[no-untyped-def]
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setToolTip(tip or text)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        self.a_new = self._action("New project…", self.new_project, QKeySequence.StandardKey.New)
        self.a_open = self._action("Open project…", self.open_project_dialog, QKeySequence.StandardKey.Open)
        self.a_save = self._action("Save", self.save_current, QKeySequence.StandardKey.Save)
        self.a_validate = self._action(
            "Validate", self.validate, "Ctrl+Shift+V", "Check the program (no files written)"
        )
        self.a_build = self._action("Build", self.build, "Ctrl+B", "Compile to build/<name>.plan.json")
        self.a_sim = self._action(
            "Simulate", self.simulate, "F5", "Build and replay the plan on the simulator"
        )
        self.a_run = self._action(
            "Run on robot", self.run_on_robot, "F6", "Stream the plan over the connected link"
        )
        self.a_estop = self._action(
            "E-STOP", self.robot_panel.emergency_stop, "Ctrl+.", "Torque-free broadcast + latch"
        )
        tb = QToolBar("Main")
        tb.setMovable(False)
        for a in (self.a_new, self.a_open, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_validate, self.a_build, self.a_sim, self.a_run):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_estop)
        self.addToolBar(tb)

        m = self.menuBar()
        f = m.addMenu("&File")
        for a in (self.a_new, self.a_open, self.a_save):
            f.addAction(a)
        f.addAction(self._action("Open plan…", self.open_plan_dialog))
        f.addSeparator()
        f.addAction(self._action("Quit", self.close, QKeySequence.StandardKey.Quit))
        b = m.addMenu("&Build")
        for a in (self.a_validate, self.a_build, self.a_sim, self.a_run, self.a_estop):
            b.addAction(a)
        lg = m.addMenu("&Legacy")
        lg.addAction(self._action("Migrate C# source…", self.migrate_dialog))
        lg.addAction(self._action("Decode hex bytes…", self.decode_dialog))
        lg.addAction(self._action("Inspect robot file (.act)…", self.inspect_robot_file))
        h = m.addMenu("&Help")
        h.addAction(self._action("Reference", lambda: self.bottom_docks["Reference"].raise_(), "F1"))
        h.addAction(self._action("About", self.about))

    # ------------------------------------------------------------------ project
    def joint_map(self) -> JointMap:
        try:
            return self.project.joint_map() if self.project else load_profile("legacy-cgenibo")
        except GeniboError as exc:
            QMessageBox.warning(self, "Joints", str(exc))
            return load_profile("legacy-cgenibo")

    def robot_id(self) -> int:
        return self.project.robot_id if self.project else 0x20

    def _show_welcome(self) -> None:
        w = QPlainTextEdit()
        w.setReadOnly(True)
        w.setFont(mono_font(12))
        w.setPlainText(
            "Genibo Studio\n\n"
            "  New project…  (Ctrl+N)  create a compiled program or a host script\n"
            "  Open project… (Ctrl+O)  open a folder containing genibo.toml\n\n"
            "Workflow: edit -> Validate (live) -> Build (Ctrl+B) -> Simulate (F5) -> Run on robot (F6)\n\n"
            "The robot never runs Python. Builds produce the exact frames the legacy\n"
            "OpenJigWare tool sent (golden-tested), streamed by this PC over Bluetooth serial.\n"
        )
        self.tabs.addTab(w, "Welcome")

    def is_trusted(self) -> bool:
        if self.project is None:
            return False
        root = str(self.project.root.resolve())
        return root in self._trusted or root in trusted_project_roots()

    def trust_project(self, *, remember: bool = True) -> None:
        """Allow this project's program to run here (validation and builds execute it)."""
        if self.project is None:
            return
        self._trusted.add(str(self.project.root.resolve()))
        if remember:
            remember_trusted_project(self.project.root)

    def _ask_trust(self) -> bool:
        if self.is_trusted():
            return True
        assert self.project is not None
        answer = QMessageBox.question(
            self,
            "Trust this project?",
            f"Building {self.project.entry} runs its Python code on this computer "
            "(that is how compiled programs are traced).\n\n"
            f"Only continue if you trust the files in\n{self.project.root}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.trust_project()
        self.validate()
        return True

    def open_project(self, root: Path, *, trusted: bool = False) -> None:
        """Open a project. Its code is only executed once the folder is trusted.

        ``trusted=True`` trusts it for this session only (projects created here,
        self-tests); folders the user approves are remembered across sessions.
        """
        try:
            self.project = Project.load(root)
        except ProjectError as exc:
            QMessageBox.warning(self, "Open project", str(exc))
            return
        self.fs_model.setRootPath(str(self.project.root))
        self.fs_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.tree.setRootIndex(self.fs_model.index(str(self.project.root)))
        self.project_lbl.setText(
            f"{self.project.name} · {self.project.kind} · robot 0x{self.project.robot_id:02x} · "
            f"joints {self.project.joint_profile}"
        )
        while self.tabs.count():
            self._close_tab(0, force=True)
        self.open_file(self.project.entry_path)
        self.setWindowTitle(f"Genibo Studio — {self.project.name}")
        if trusted:
            self.trust_project(remember=False)
        plan_file = self.project.plan_path
        if plan_file.exists():
            try:
                self._set_plan(CommandPlan.load(plan_file))
            except (ValueError, KeyError) as exc:
                self.statusBar().showMessage(f"existing plan not loaded: {exc}", 8000)
        self.validate()

    def open_project_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Open Genibo project (folder with genibo.toml)")
        if d:
            self.open_project(Path(d))

    def new_project(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, "Parent folder for the new project")
        if not parent:
            return
        name, ok = QInputDialog.getText(self, "New project", "Project name:", text="my-genibo")
        if not ok or not name:
            return
        kind, ok = QInputDialog.getItem(
            self,
            "New project",
            "Kind:",
            ["program (compiled to a command plan)", "script (host script, live link)"],
            0,
            False,
        )
        if not ok:
            return
        try:
            proj = create_project(Path(parent) / name, name, kind=kind.split()[0])
        except GeniboError as exc:
            QMessageBox.warning(self, "New project", str(exc))
            return
        remember_trusted_project(proj.root)  # created here from our own template
        self.open_project(proj.root, trusted=True)

    def open_plan_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open command plan", filter="Plans (*.plan.json *.json)")
        if path:
            try:
                self._set_plan(CommandPlan.load(Path(path)))
                self.bottom_docks["Plan"].raise_()
            except (ValueError, KeyError) as exc:
                QMessageBox.warning(self, "Open plan", str(exc))

    # ------------------------------------------------------------------ editors
    def open_file(self, path: Path) -> None:
        path = path.resolve()
        if path in self.editors:
            self.tabs.setCurrentWidget(self.editors[path])
            return
        if not path.exists():
            return
        ed = CodeEditor()
        ed.setPlainText(path.read_text(encoding="utf-8", errors="replace"))
        ed.document().setModified(False)
        ed.textChanged.connect(self._schedule_validate)
        ed.document().modificationChanged.connect(lambda _m, p=path: self._update_tab_title(p))
        self.editors[path] = ed
        welcome = [i for i in range(self.tabs.count()) if self.tabs.tabText(i) == "Welcome"]
        for i in reversed(welcome):
            self.tabs.removeTab(i)
        self.tabs.addTab(ed, path.name)
        self.tabs.setCurrentWidget(ed)

    def _tree_open(self, index) -> None:  # type: ignore[no-untyped-def]
        p = Path(self.fs_model.filePath(index))
        if p.is_file() and p.suffix in (".py", ".toml", ".lst", ".md", ".json"):
            self.open_file(p)

    def _update_tab_title(self, path: Path) -> None:
        ed = self.editors.get(path)
        if ed is not None:
            i = self.tabs.indexOf(ed)
            self.tabs.setTabText(i, path.name + (" •" if ed.document().isModified() else ""))

    def _close_tab(self, i: int, force: bool = False) -> None:
        w = self.tabs.widget(i)
        path = next((p for p, e in self.editors.items() if e is w), None)
        if path is not None and not force and self.editors[path].document().isModified():
            r = QMessageBox.question(
                self,
                "Unsaved changes",
                f"Save {path.name}?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Save:
                path.write_text(self.editors[path].toPlainText(), encoding="utf-8")
        if path is not None:
            del self.editors[path]
        self.tabs.removeTab(i)

    def save_current(self) -> None:
        for path, ed in self.editors.items():
            if ed.document().isModified():
                path.write_text(ed.toPlainText(), encoding="utf-8")
                ed.document().setModified(False)
        if self.project is not None and (self.project.root / "genibo.toml") in self.editors:
            self.open_project(self.project.root)
        self.statusBar().showMessage("saved", 3000)

    def entry_editor(self) -> CodeEditor | None:
        return self.editors.get(self.project.entry_path.resolve()) if self.project else None

    def _goto_entry_line(self, line: int) -> None:
        if self.project is None:
            return
        self.open_file(self.project.entry_path)
        ed = self.entry_editor()
        if ed is not None:
            ed.goto_line(line)

    # ------------------------------------------------------------------ validate / build / simulate / run
    def _schedule_validate(self) -> None:
        self._validate_timer.start()

    def _compile(self) -> BuildResult | None:
        if self.project is None:
            return None
        ed = self.entry_editor()
        source = ed.toPlainText() if ed is not None else self.project.entry_path.read_text(encoding="utf-8")
        if self.project.kind == "script":
            try:
                ast.parse(source, filename=str(self.project.entry_path))
                return BuildResult(
                    [Diagnostic("info", "S000", "host script: syntax OK (runs live; nothing to compile)")]
                )
            except SyntaxError as exc:
                return BuildResult(
                    [
                        Diagnostic(
                            "error",
                            "G100",
                            f"syntax error: {exc.msg}",
                            SourceLocation(str(self.project.entry_path), exc.lineno or 1),
                        )
                    ]
                )
        if not self.is_trusted():
            _tree, diags = check_source(source, str(self.project.entry_path))
            diags.append(
                Diagnostic(
                    "info",
                    "S100",
                    "project not trusted: only static checks ran; its code runs once you Build "
                    "(you will be asked to trust the folder)",
                )
            )
            return BuildResult(diags)
        return compile_source(
            source,
            str(self.project.entry_path),
            joints=self.joint_map(),
            robot_id=self.project.robot_id,
            encoding=self.project.encoding,
            entry=self.project.program,
            display_path=self.project.entry,
            time_limit_s=3.0,
        )

    def _show_result(self, result: BuildResult) -> None:
        self.last_build = result
        self.problems.set_diagnostics(result.diagnostics)
        ed = self.entry_editor()
        if ed is not None:
            ed.set_diagnostics(result.diagnostics)
        n_e = len(result.errors)
        n_w = sum(d.severity == "warning" for d in result.diagnostics)
        color = PALETTE["error"] if n_e else (PALETTE["warning"] if n_w else PALETTE["ok"])
        self.build_lbl.setText(f"● {n_e} errors · {n_w} warnings")
        self.build_lbl.setStyleSheet(f"color:{color}")

    def validate(self) -> BuildResult | None:
        result = self._compile()
        if result is not None:
            self._show_result(result)
        return result

    def build(self) -> CommandPlan | None:
        if self.project is None:
            QMessageBox.information(self, "Build", "Open or create a project first.")
            return None
        self.save_current()
        if self.project.kind == "script":
            QMessageBox.information(
                self,
                "Build",
                "Script projects run live on the host; nothing to compile.\n"
                "Use `genibo script src/main.py --sim` or the Robot panel.",
            )
            return None
        if not self._ask_trust():
            self.statusBar().showMessage("build cancelled: project not trusted", 6000)
            return None
        result = self.project.build()
        self._show_result(result)
        if not result.ok or result.plan is None:
            self.bottom_docks["Problems"].raise_()
            self.statusBar().showMessage("build failed", 6000)
            self._set_plan(None)
            return None
        self._set_plan(result.plan)
        self.bottom_docks["Plan"].raise_()
        self.statusBar().showMessage(
            f"built {self.project.plan_path.name}: {len(result.plan.frames)} frames", 6000
        )
        return result.plan

    def _set_plan(self, plan: CommandPlan | None) -> None:
        self.plan = plan
        self.plan_panel.set_plan(plan)

    def simulate(self) -> None:
        plan = self.build() if self.project is not None else self.plan
        if plan is None:
            return
        files = [
            (int(s["body"]["storage"]), str(s["body"]["name"]))
            for s in plan.steps
            if s["body"]["kind"] == "play_file"
        ]
        self.sim_panel.simulate(plan, self.joint_map(), files)
        if files:
            self.sim_panel.events.insertItem(
                0, "assumed present on the robot: " + ", ".join(n for _, n in files)
            )
        self.bottom_docks["Simulator"].raise_()

    def run_on_robot(self) -> None:
        plan = self.build() if self.project is not None else self.plan
        if plan is not None:
            self.robot_panel.run_plan(plan)

    def _on_connection(self, mode: str, desc: str) -> None:
        text = {
            "off": "link: disconnected",
            "sim": f"link: SIMULATOR · {desc}",
            "hw": f"link: HARDWARE · {desc}",
        }[mode]
        self.link_lbl.setText(text)
        color = {"off": PALETTE["muted"], "sim": PALETTE["simulated"], "hw": PALETTE["hardware"]}[mode]
        self.link_lbl.setStyleSheet(f"color:{color}; font-weight:600")

    # ------------------------------------------------------------------ legacy tools
    def _text_dialog(self, title: str, text: str, extra: QWidget | None = None) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(820, 600)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setFont(mono_font(11))
        lay = QVBoxLayout(dlg)
        lay.addWidget(view)
        row = QHBoxLayout()
        row.addStretch(1)
        if extra is not None:
            row.addWidget(extra)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        row.addWidget(close)
        lay.addLayout(row)
        dlg.exec()

    def migrate_dialog(self) -> None:
        from genibo.legacy.migrate import scan

        path, _ = QFileDialog.getOpenFileName(self, "Legacy C# source", filter="C# (*.cs)")
        if not path:
            return
        report = scan(Path(path))
        save = QPushButton("Save Python skeleton…")

        def do_save() -> None:
            dest, _ = QFileDialog.getSaveFileName(self, "Save skeleton", "migrated.py", "Python (*.py)")
            if dest:
                Path(dest).write_text(report.skeleton(), encoding="utf-8")
                self.statusBar().showMessage(f"wrote {dest}", 5000)

        save.clicked.connect(do_save)
        self._text_dialog("Migration report", report.render(), save)

    def decode_dialog(self) -> None:
        from genibo.protocol.describe import describe_stream

        text, ok = QInputDialog.getMultiLineText(self, "Decode hex", "Bytes (hex, spaces allowed):")
        if not ok or not text.strip():
            return
        try:
            data = bytes.fromhex(text.replace(":", " "))
        except ValueError as exc:
            QMessageBox.warning(self, "Decode", str(exc))
            return
        self._text_dialog("Decoded frames", "\n\n".join(str(d) for d in describe_stream(data)))

    def inspect_robot_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Robot-side file (.act / task)")
        if not path:
            return
        data = Path(path).read_bytes()
        lines = [
            f"{Path(path).name}: {len(data)} bytes",
            "The .act/task formats are not documented in the legacy sources; the SDK treats them as opaque",
            "bytes (upload/download/list/play only). Hex dump of the first 4 KiB:",
            "",
        ]
        for off in range(0, min(len(data), 4096), 16):
            chunk = data[off : off + 16]
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{off:08x}  {chunk.hex(' '):<47}  {asc}")
        self._text_dialog("Robot file (opaque)", "\n".join(lines))

    def about(self) -> None:
        from genibo import __version__

        QMessageBox.about(
            self,
            "Genibo Studio",
            f"Genibo SDK {__version__}\n\nPython-first tooling for the Genibo "
            "robot. Wire format verified byte-for-byte against the OpenJigWare C# implementation; "
            "hardware behaviour is verified only on a physical robot.",
        )

    def closeEvent(self, e: QCloseEvent) -> None:
        dirty = [p.name for p, ed in self.editors.items() if ed.document().isModified()]
        if dirty:
            r = QMessageBox.question(self, "Quit", f"Unsaved: {', '.join(dirty)}. Quit anyway?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        if self.robot_panel.robot is not None:
            self.robot_panel.robot.close()
        logging.getLogger("genibo").removeHandler(self.log_panel.handler)
        e.accept()
