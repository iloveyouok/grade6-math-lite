import ast
import sys
import subprocess
from fractions import Fraction
import math
from typing import Tuple

SAFE_MODULES = {"math", "fractions", "decimal"}
FORBIDDEN_CALLS = {"eval", "exec", "open", "__import__", "compile", "globals", "locals", "getattr", "setattr", "delattr"}

class SecurityVisitor(ast.NodeVisitor):
    def __init__(self):
        self.is_safe = True
        self.violations = []

    def visit_Import(self, node):
        for alias in node.names:
            root_module = alias.name.split(".")[0]
            if root_module not in SAFE_MODULES:
                self.is_safe = False
                self.violations.append(f"Forbidden import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            root_module = node.module.split(".")[0]
            if root_module not in SAFE_MODULES:
                self.is_safe = False
                self.violations.append(f"Forbidden from-import: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                self.is_safe = False
                self.violations.append(f"Forbidden call: {node.func.id}")
        self.generic_visit(node)

def static_check_code(code: str) -> Tuple[bool, str]:
    """
    Checks Python code AST for unsafe imports or calls.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    visitor = SecurityVisitor()
    visitor.visit(tree)
    if not visitor.is_safe:
        return False, "; ".join(visitor.violations)
    return True, "Safe"

def _normalize_math_value(val_str: str):
    """
    Tries to convert a string representation of fraction or decimal/int into a Fraction.
    """
    cleaned = val_str.strip().strip("'\"").replace(" ", "")
    # Check if there is Fraction(...)
    if "/" in cleaned:
        try:
            return Fraction(cleaned)
        except Exception:
            pass
    try:
        return Fraction(cleaned)
    except Exception:
        try:
            return float(cleaned)
        except Exception:
            return cleaned

def are_values_equivalent(val1: str, val2: str) -> bool:
    """
    Compares two arithmetic values for equivalence (e.g. 3/4 == 0.75, 6 == 6.0).
    """
    n1 = _normalize_math_value(val1)
    n2 = _normalize_math_value(val2)

    if isinstance(n1, Fraction) and isinstance(n2, Fraction):
        return n1 == n2
    if (isinstance(n1, (Fraction, float, int))) and (isinstance(n2, (Fraction, float, int))):
        return math.isclose(float(n1), float(n2), abs_tol=1e-5)
    return str(val1).strip() == str(val2).strip()

def run_math_sandbox(expected_answer: str, verify_code: str, timeout: float = 2.0) -> Tuple[bool, str]:
    """
    Executes verify_code in an isolated sub-process with 2s timeout.
    Returns (is_verified, message).
    """
    is_safe, reason = static_check_code(verify_code)
    if not is_safe:
        return False, f"Security check failed: {reason}"

    try:
        res = subprocess.run(
            [sys.executable, "-I", "-c", verify_code],
            timeout=timeout,
            capture_output=True,
            text=True
        )
    except subprocess.TimeoutExpired:
        return False, f"Execution timed out (> {timeout}s)"
    except Exception as e:
        return False, f"Subprocess execution error: {e}"

    if res.returncode != 0:
        return False, f"Process returned non-zero code {res.returncode}: {res.stderr.strip()}"

    output = res.stdout.strip()
    if not output:
        return False, "Process produced empty output"

    # Take the last line of output as result
    last_line = output.splitlines()[-1].strip()

    if are_values_equivalent(expected_answer, last_line):
        return True, f"Verified: {last_line} matches expected {expected_answer}"
    else:
        return False, f"Answer mismatch: output '{last_line}' != expected '{expected_answer}'"
