"""Static (AST) checks that enforce the compiled-program model before any code runs.

This is a correctness guard, not a security sandbox: program files are the
user's own code and run on the user's machine at build time.
"""

from __future__ import annotations

import ast
import difflib

from genibo.errors import Diagnostic, SourceLocation
from genibo.program.builder import PROGRAM_METHODS

ALLOWED_IMPORTS = {"genibo", "genibo.program", "math", "__future__", "typing"}

FORBIDDEN_CALLS = {
    "open": "file access happens at build time on the PC, not on the robot",
    "input": "programs are compiled ahead of time and cannot ask for input",
    "eval": "dynamic code cannot be validated",
    "exec": "dynamic code cannot be validated",
    "compile": "dynamic code cannot be validated",
    "__import__": "only genibo and math may be imported",
    "globals": "not supported in compiled programs",
    "locals": "not supported in compiled programs",
    "vars": "not supported in compiled programs",
    "setattr": "not supported in compiled programs",
    "delattr": "not supported in compiled programs",
}


def _loc(path: str, node: ast.AST) -> SourceLocation:
    return SourceLocation(path, getattr(node, "lineno", 1), getattr(node, "col_offset", 0))


def _decorated_as_program(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Name) and target.id == "program":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "program":
            return True
    return False


class _Checker(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.diags: list[Diagnostic] = []
        self.program_params: list[str] = []

    def err(self, node: ast.AST, code: str, msg: str, hint: str = "") -> None:
        self.diags.append(Diagnostic("error", code, msg, _loc(self.path, node), hint))

    def warn(self, node: ast.AST, code: str, msg: str, hint: str = "") -> None:
        self.diags.append(Diagnostic("warning", code, msg, _loc(self.path, node), hint))

    # imports
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.name not in ALLOWED_IMPORTS:
                self.err(
                    node,
                    "G101",
                    f"import of {a.name!r} is not allowed in a compiled program",
                    "compiled programs may import only genibo and math; robot-time logic belongs in a host script",
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if node.level or mod not in ALLOWED_IMPORTS:
            self.err(
                node,
                "G101",
                f"import from {mod or '.'!r} is not allowed in a compiled program",
                "compiled programs may import only genibo and math",
            )

    # control flow that cannot be decided at build time
    def visit_While(self, node: ast.While) -> None:
        self.err(
            node,
            "G102",
            "while loops are not supported in compiled programs",
            "use 'for i in range(n)'; a loop that waits for the robot belongs in a host script",
        )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.err(node, "G103", "async functions are not supported in compiled programs")

    def visit_Await(self, node: ast.Await) -> None:
        self.err(node, "G103", "await is not supported in compiled programs")

    def visit_Yield(self, node: ast.Yield) -> None:
        self.err(node, "G104", "generators are not supported in compiled programs")

    visit_YieldFrom = visit_Yield  # type: ignore[assignment]

    def visit_Global(self, node: ast.Global) -> None:
        self.err(node, "G105", "global statements are not supported in compiled programs")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.err(node, "G105", "nonlocal statements are not supported in compiled programs")

    def visit_Try(self, node: ast.Try) -> None:
        self.warn(node, "G202", "try/except runs at build time only; robot-side errors cannot be caught here")
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self.err(node, "G106", "with-statements are not supported in compiled programs")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                self.err(node, "G107", f"{node.func.id}() is not allowed: {FORBIDDEN_CALLS[node.func.id]}")
            elif node.func.id == "print":
                self.warn(node, "G201", "print() runs once at build time, not on the robot")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if _decorated_as_program(node):
            args = node.args.posonlyargs + node.args.args
            if len(args) != 1:
                self.err(
                    node,
                    "G108",
                    f"@program function {node.name!r} must take exactly one parameter (the Program)",
                )
            else:
                self.program_params.append(args[0].arg)
        self.generic_visit(node)

    def check_program_attributes(self, tree: ast.AST) -> None:
        params = set(self.program_params)
        if not params:
            return
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in params
                and node.attr not in PROGRAM_METHODS
            ):
                close = difflib.get_close_matches(node.attr, PROGRAM_METHODS, n=1)
                self.err(
                    node,
                    "G109",
                    f"Program has no step {node.attr!r}",
                    f"did you mean {close[0]!r}?"
                    if close
                    else f"available: {', '.join(sorted(PROGRAM_METHODS))}",
                )


def check_source(source: str, path: str) -> tuple[ast.Module | None, list[Diagnostic]]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        loc = SourceLocation(path, exc.lineno or 1, max((exc.offset or 1) - 1, 0))
        return None, [Diagnostic("error", "G100", f"syntax error: {exc.msg}", loc)]
    c = _Checker(path)
    c.visit(tree)
    c.check_program_attributes(tree)
    if not c.program_params and not any(d.code == "G108" for d in c.diags):
        c.diags.append(
            Diagnostic(
                "error",
                "G110",
                "no @program function found",
                SourceLocation(path, 1),
                "decorate the entry function with @program",
            )
        )
    return tree, c.diags
