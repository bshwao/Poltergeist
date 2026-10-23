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
CASE_NAMES = [node.name for node in ast.parse(CASES.read_text(encoding="utf-8")).body
              if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]


@pytest.mark.parametrize("case", CASE_NAMES)
def test_gui_application_process(case, tmp_path):
    report = tmp_path / "gui-case.xml"
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(CASES), "-k", case,
                             f"--junitxml={report}"], capture_output=True, text=True, timeout=120)
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
