"""Studio colours and stylesheet (one neutral palette; status colours carry meaning)."""

from __future__ import annotations

PALETTE: dict[str, str] = {
    "bg": "#1d1f23",
    "panel": "#24272c",
    "panel_alt": "#2b2f35",
    "border": "#383d45",
    "text": "#d7dae0",
    "muted": "#8b929c",
    "accent": "#5aa9e6",
    "gutter": "#202227",
    "gutter_text": "#5f6670",
    "current_line": "#2a2e35",
    "kw": "#c792ea",
    "builtin": "#82aaff",
    "decorator": "#f78c6c",
    "call": "#82aaff",
    "step": "#7fdbca",
    "number": "#f78c6c",
    "string": "#c3e88d",
    "comment": "#6b7280",
    "error": "#ef5350",
    "warning": "#f6c445",
    "info": "#5aa9e6",
    "ok": "#4caf7a",
    "hardware": "#e0563b",
    "simulated": "#3b8fd9",
}

#: Distinct series colours for joint plots.
SERIES = ["#5aa9e6", "#f6c445", "#7fdbca", "#f78c6c", "#c792ea", "#c3e88d", "#ef5350", "#82aaff"]

STYLESHEET = f"""
QWidget {{ background: {PALETTE["bg"]}; color: {PALETTE["text"]}; font-size: 13px; }}
QMainWindow::separator {{ background: {PALETTE["border"]}; width: 1px; height: 1px; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {PALETTE["panel"]}; padding: 6px 8px; color: {PALETTE["muted"]};
    font-weight: 600; letter-spacing: 0.5px; }}
QToolBar {{ background: {PALETTE["panel"]}; border: none; border-bottom: 1px solid {PALETTE["border"]};
    padding: 4px; spacing: 4px; }}
QToolButton {{ padding: 5px 10px; border-radius: 5px; }}
QToolButton:hover {{ background: {PALETTE["panel_alt"]}; }}
QPushButton {{ background: {PALETTE["panel_alt"]}; border: 1px solid {PALETTE["border"]}; border-radius: 5px;
    padding: 5px 12px; }}
QPushButton:hover {{ border-color: {PALETTE["accent"]}; }}
QPushButton:disabled {{ color: {PALETTE["muted"]}; }}
QPushButton#estop {{ background: #8e1f1f; border-color: #c62828; color: white; font-weight: 700; padding: 8px; }}
QPushButton#estop:hover {{ background: #b71c1c; }}
QPushButton#primary {{ background: #1f5f8b; border-color: {PALETTE["accent"]}; color: white; font-weight: 600; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {PALETTE["panel"]}; border: 1px solid {PALETTE["border"]};
    border-radius: 4px; padding: 4px 6px; }}
QPlainTextEdit, QTextBrowser, QTreeView, QTableWidget, QListWidget {{ background: {PALETTE["panel"]};
    border: 1px solid {PALETTE["border"]}; selection-background-color: #2f4f6f; }}
QHeaderView::section {{ background: {PALETTE["panel_alt"]}; color: {PALETTE["muted"]}; border: none;
    border-right: 1px solid {PALETTE["border"]}; padding: 4px 6px; }}
QTabWidget::pane {{ border: 1px solid {PALETTE["border"]}; }}
QTabBar::tab {{ background: {PALETTE["panel"]}; padding: 6px 14px; border: 1px solid {PALETTE["border"]};
    border-bottom: none; color: {PALETTE["muted"]}; }}
QTabBar::tab:selected {{ background: {PALETTE["panel_alt"]}; color: {PALETTE["text"]}; }}
QStatusBar {{ background: {PALETTE["panel"]}; border-top: 1px solid {PALETTE["border"]}; color: {PALETTE["muted"]}; }}
QLabel#badge {{ border-radius: 4px; padding: 2px 8px; font-weight: 700; color: white; }}
QLabel#muted {{ color: {PALETTE["muted"]}; }}
QGroupBox {{ border: 1px solid {PALETTE["border"]}; border-radius: 6px; margin-top: 14px; padding: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {PALETTE["muted"]}; }}
QSlider::groove:horizontal {{ height: 4px; background: {PALETTE["border"]}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {PALETTE["accent"]}; width: 12px; margin: -5px 0; border-radius: 6px; }}
"""
