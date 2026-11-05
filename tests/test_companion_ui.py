"""Run each real GUI case in its own application process.

Tcl interpreters must not be finalized by an unrelated API worker thread after
the GUI has closed. The product exits its process at that boundary; the test
driver follows that lifecycle while preserving each callback/assertion case.
"""

import ast
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

CASES = Path(__file__).with_name("companion_ui_cases.py")
CASE_NAMES = []
for node in ast.parse(CASES.read_text(encoding="utf-8")).body:
    if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
        continue
    # Each scaling parameter needs its own Tcl process too. These cases use
    # literal window scales and pytest's default IDs; no GUI is imported here.
    scales = None
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "parametrize":
            assert ast.literal_eval(decorator.args[0]) == "window"
            scales = ast.literal_eval(decorator.args[1])
    CASE_NAMES.extend([node.name] if scales is None else [f"{node.name}[{scale}]" for scale in scales])


@pytest.mark.parametrize("case", CASE_NAMES)
def test_gui_application_process(case, tmp_path):
    report = tmp_path / "gui-case.xml"
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", f"{CASES}::{case}",
                             f"--junitxml={report}"], capture_output=True, text=True, timeout=360)
    output = result.stdout + "\n" + result.stderr
    assert result.returncode in (0, 5), output
    assert "Windows fatal exception" not in output and "Fatal Python error" not in output, output
    assert "PytestUnraisableExceptionWarning" not in output, output
    assert report.is_file(), output
    testcases = ET.parse(report).getroot().findall(".//testcase")
    assert len(testcases) == 1, output
    skipped = testcases[0].find("skipped")
    if skipped is not None:
        pytest.skip(skipped.attrib.get("message", "GUI prerequisite unavailable"))
    assert result.returncode == 0, output
    assert testcases[0].find("failure") is None and testcases[0].find("error") is None, output
