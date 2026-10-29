"""Windows Authenticode verification with an independently pinned publisher."""

import json
import os
from pathlib import Path
import re
import subprocess


def powershell(script, *, variables):
    if os.name != "nt":
        raise ValueError("Release signature operations require Windows")
    executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    environment = dict(os.environ)
    # A parent PowerShell 7 process may omit Windows PowerShell's modules.
    # Load only the installed Windows modules, never user/plugin module paths.
    environment["PSModulePath"] = str(executable.parent / "Modules")
    environment.update(variables)
    # Paths and certificate selectors are environment data, never command text.
    return subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
        env=environment, capture_output=True, text=True, check=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout


def signature_details(path):
    script = """$ErrorActionPreference='Stop';
$s=Get-AuthenticodeSignature -LiteralPath $env:POLTERGEIST_SIGNATURE_FILE;
$pin=''; if ($s.SignerCertificate) {
 $h=[System.Security.Cryptography.SHA256]::Create();
 try { $pin=([BitConverter]::ToString($h.ComputeHash($s.SignerCertificate.RawData))).Replace('-','').ToLowerInvariant() } finally { $h.Dispose() }
};
@{status=[string]$s.Status;publisher_sha256=$pin;timestamp_present=($null -ne $s.TimeStamperCertificate)} | ConvertTo-Json -Compress
"""
    return json.loads(powershell(script, variables={"POLTERGEIST_SIGNATURE_FILE": str(Path(path).absolute())}))


def verify_signature(path, publishers):
    if not publishers or any(not re.fullmatch(r"[0-9a-f]{64}", pin) for pin in publishers):
        raise ValueError("No trusted release publisher is configured")
    try:
        result = signature_details(path)
        if result["status"] != "Valid" or result["publisher_sha256"] not in publishers or result["timestamp_present"] is not True:
            raise ValueError
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        raise ValueError("Release signature, timestamp or publisher verification failed") from None
    return result["publisher_sha256"]
