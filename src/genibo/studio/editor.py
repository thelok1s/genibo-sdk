"""Python code editor widget: line numbers, syntax highlighting, diagnostic markers."""

from __future__ import annotations

import keyword
import re

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from genibo.errors import Diagnostic
from genibo.studio.theme import PALETTE


def mono_font(size: int = 12) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSize(size)
    return f


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.DemiBold)
    f.setFontItalic(italic)
    return f


class PythonHighlighter(QSyntaxHighlighter):
    """Small, fast Python highlighter (keywords, builtins, strings, numbers, comments, g.* steps)."""

    def __init__(self, doc: QTextDocument) -> None:
        super().__init__(doc)
        p = PALETTE
        self.rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        kw = r"\b(" + "|".join(keyword.kwlist) + r")\b"
        self.rules.append((QRegularExpression(kw), _fmt(p["kw"], bold=True)))
        builtins = r"\b(range|len|print|int|float|str|bool|list|dict|tuple|abs|min|max|round|enumerate|zip)\b"
        self.rules.append((QRegularExpression(builtins), _fmt(p["builtin"])))
        self.rules.append((QRegularExpression(r"@\w+"), _fmt(p["decorator"], bold=True)))
        self.rules.append((QRegularExpression(r"\b\w+(?=\()"), _fmt(p["call"])))
        self.rules.append((QRegularExpression(r"\b(g|robot)\.\w+"), _fmt(p["step"], bold=True)))
        self.rules.append((QRegularExpression(r"\b(0x[0-9A-Fa-f]+|\d+(\.\d+)?)\b"), _fmt(p["number"])))
        self.str_fmt = _fmt(p["string"])
        self.comment_fmt = _fmt(p["comment"], italic=True)
        self.rules.append((QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), self.str_fmt))
        self.rules.append((QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), self.str_fmt))
        self.tri = re.compile(r'"""|\'\'\'')

    def highlightBlock(self, text: str) -> None:
        for rx, fmt in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
        # comments (outside strings, good enough for highlighting)
        idx = text.find("#")
        if idx >= 0 and text[:idx].count('"') % 2 == 0 and text[:idx].count("'") % 2 == 0:
            self.setFormat(idx, len(text) - idx, self.comment_fmt)
        # triple-quoted strings spanning blocks
        self.setCurrentBlockState(0)
        start = 0
        inside = self.previousBlockState() == 1
        pos = 0
        while True:
            m = self.tri.search(text, pos)
            if inside:
                end = m.end() if m else len(text)
                self.setFormat(start, end - start, self.str_fmt)
                if not m:
                    self.setCurrentBlockState(1)
                    return
                inside = False
                pos = end
            else:
                if not m:
                    return
                start = m.start()
                inside = True
                pos = m.end()


class _LineArea(QWidget):
    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_area_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self.editor.paint_line_area(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._area = _LineArea(self)
        self._diags: dict[int, Diagnostic] = {}
        self.highlighter = PythonHighlighter(self.document())
        self.blockCountChanged.connect(lambda _n: self._update_margin())
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._refresh_selections)
        self._update_margin()
        self._refresh_selections()

    # ------------------------------------------------------------ editing conveniences
    def keyPressEvent(self, e) -> None:  # type: ignore[no-untyped-def]
        if e.key() == Qt.Key.Key_Tab and not e.modifiers():
            self.insertPlainText("    ")
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            line = self.textCursor().block().text()
            indent = len(line) - len(line.lstrip(" "))
            if line.rstrip().endswith(":"):
                indent += 4
            super().keyPressEvent(e)
            self.insertPlainText(" " * indent)
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ line numbers
    def line_area_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self) -> None:
        self.setViewportMargins(self.line_area_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self._area.scroll(0, dy)
        else:
            self._area.update(0, rect.y(), self._area.width(), rect.height())

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._area.setGeometry(QRect(cr.left(), cr.top(), self.line_area_width(), cr.height()))

    def paint_line_area(self, event: QPaintEvent) -> None:
        p = QPainter(self._area)
        p.fillRect(event.rect(), QColor(PALETTE["gutter"]))
        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line = n + 1
                d = self._diags.get(line)
                if d is not None:
                    color = PALETTE["error"] if d.severity == "error" else PALETTE["warning"]
                    p.setBrush(QColor(color))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(3, top + h // 2 - 3, 6, 6)
                p.setPen(QColor(PALETTE["gutter_text"]))
                p.drawText(0, top, self._area.width() - 6, h, Qt.AlignmentFlag.AlignRight, str(line))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            n += 1

    # ------------------------------------------------------------ diagnostics
    def set_diagnostics(self, diags: list[Diagnostic]) -> None:
        self._diags = {}
        for d in diags:
            if d.location is None:
                continue
            prev = self._diags.get(d.location.line)
            if prev is None or (prev.severity != "error" and d.severity == "error"):
                self._diags[d.location.line] = d
        self._refresh_selections()
        self._area.update()

    def _refresh_selections(self) -> None:
        sels = []
        cur = QTextEdit.ExtraSelection()
        cur.format.setBackground(QColor(PALETTE["current_line"]))  # type: ignore[attr-defined]
        cur.format.setProperty(QTextFormat.Property.FullWidthSelection, True)  # type: ignore[attr-defined]
        cur.cursor = self.textCursor()  # type: ignore[attr-defined]
        cur.cursor.clearSelection()  # type: ignore[attr-defined]
        sels.append(cur)
        doc = self.document()
        for line, d in self._diags.items():
            block = doc.findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            s = QTextEdit.ExtraSelection()
            color = QColor(PALETTE["error"] if d.severity == "error" else PALETTE["warning"])
            s.format.setUnderlineColor(color)  # type: ignore[attr-defined]
            s.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)  # type: ignore[attr-defined]
            s.format.setToolTip(f"{d.code}: {d.message}")  # type: ignore[attr-defined]
            c = QTextCursor(block)
            c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            text = block.text()
            c.movePosition(QTextCursor.MoveOperation.Right, n=len(text) - len(text.lstrip()))
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            s.cursor = c  # type: ignore[attr-defined]
            sels.append(s)
        self.setExtraSelections(sels)

    def goto_line(self, line: int) -> None:
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            c = QTextCursor(block)
            self.setTextCursor(c)
            self.centerCursor()
            self.setFocus()
