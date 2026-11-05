"""Create timestamped signed release metadata using a Windows store certificate."""

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from companion.signatures import powershell, verify_signature
from companion.updates import ReleaseManifest, file_digest
from backend.migrations import CURRENT_REVISION


def signing_inputs(tool, thumbprint, timestamp_url, publisher, *, allow_http=False):
    tool = Path(tool).absolute()
    url = urlsplit(timestamp_url)
    if (not re.fullmatch(r"[0-9A-Fa-f]{40}", thumbprint)
            or not re.fullmatch(r"[0-9a-f]{64}", publisher)
            or url.scheme not in (("http", "https") if allow_http else ("https",)) or not url.hostname or url.username or url.password or url.fragment
            or not re.fullmatch(r"https?://[A-Za-z0-9:./_-]+", timestamp_url)
            or any(c in str(tool) for c in '$"\r\n')):
        raise ValueError("Invalid public signing configuration")
    return {"tool": tool, "thumbprint": thumbprint.upper(), "timestamp_url": timestamp_url, "publisher": publisher}


def metadata_signing_inputs(thumbprint, timestamp_url, publisher):
    inputs = signing_inputs("signtool.exe", thumbprint, timestamp_url, publisher, allow_http=True)
    if urlsplit(timestamp_url).scheme != "http":
        raise ValueError("Windows PowerShell 5.1 metadata signing requires an http:// timestamp service")
    return inputs


def check_signing_certificate(thumbprint, publisher):
    """Read signing metadata from CurrentUser/My without opening the key."""
    if not re.fullmatch(r"[0-9A-Fa-f]{40}", thumbprint) or not re.fullmatch(r"[0-9a-f]{64}", publisher):
        raise ValueError("Invalid public certificate selector or publisher fingerprint")
    script = """$ErrorActionPreference='Stop';
$path='Cert:\\CurrentUser\\My\\' + $env:POLTERGEIST_SIGNING_THUMBPRINT;
if (-not (Test-Path -LiteralPath $path)) { '{"present":false}'; return }
$cert=Get-Item -LiteralPath $path;
$hash=[System.Security.Cryptography.SHA256]::Create();
try { $pin=([BitConverter]::ToString($hash.ComputeHash($cert.RawData))).Replace('-','').ToLowerInvariant() }
finally { $hash.Dispose() }
$now=[DateTime]::UtcNow;
$eku=@($cert.Extensions | Where-Object { $_ -is [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] });
$usage=@($cert.Extensions | Where-Object { $_ -is [System.Security.Cryptography.X509Certificates.X509KeyUsageExtension] });
$digitalSignature=[System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature;
@{present=$true; publisher_sha256=$pin; has_private_key=$cert.HasPrivateKey;
  valid_now=($cert.NotBefore.ToUniversalTime() -le $now -and $now -lt $cert.NotAfter.ToUniversalTime());
  allows_signing=(($eku.Count -eq 0 -or $eku.EnhancedKeyUsages.Value -contains '1.3.6.1.5.5.7.3.3') -and
                  ($usage.Count -eq 0 -or ($usage[0].KeyUsages -band $digitalSignature)))} | ConvertTo-Json -Compress
"""
    try:
        info = json.loads(powershell(script, variables={"POLTERGEIST_SIGNING_THUMBPRINT": thumbprint.upper()}))
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ValueError("Could not inspect the Windows current-user certificate store") from None
    if not isinstance(info, dict) or type(info.get("present")) is not bool:
        raise ValueError("Could not inspect the Windows current-user certificate store")
    if not info["present"]:
        raise ValueError("The selected certificate was not found in the Windows current-user My store")
    if info.get("publisher_sha256") != publisher:
        raise ValueError("The selected certificate does not match the configured publisher fingerprint")
    if info.get("valid_now") is not True:
        raise ValueError("The selected publisher certificate is outside its validity period")
    if info.get("allows_signing") is not True:
        raise ValueError("The selected publisher certificate does not allow code signing")
    if info.get("has_private_key") is not True:
        raise ValueError("The selected publisher certificate has no associated signing key")


def signed_metadata(installer, bundle_digest, output, *, version, thumbprint, timestamp_url, publisher,
                    installer_url=None, rollout_percent=100, rollout_salt=""):
    inputs = metadata_signing_inputs(thumbprint, timestamp_url, publisher)
    installer, output = Path(installer), Path(output)
    if output.suffix.lower() != ".ps1":
        raise ValueError("Signed release metadata uses the .ps1 Authenticode format")
    if installer_url is not None:
        from companion.update_delivery import delivery_origin
        delivery_origin(installer_url)
    verify_signature(installer, [publisher])
    now = datetime.now(UTC)
    release = ReleaseManifest(version=version, created_at=now, expires_at=now + timedelta(days=14),
        installer_bytes=installer.stat().st_size, installer_sha256=file_digest(installer),
        profile_schema_min=6, profile_schema_max=CURRENT_REVISION,
        bundle_manifest_sha256=bundle_digest, installer_url=installer_url,
        rollout_percent=rollout_percent, rollout_salt=rollout_salt)
    with output.open("x", encoding="utf-8-sig", newline="\r\n") as stream:
        stream.write("# Poltergeist release metadata v1\n# " + release.model_dump_json() + "\n")
    script = """$ErrorActionPreference='Stop';
$c=Get-Item -LiteralPath ('Cert:\\CurrentUser\\My\\' + $env:POLTERGEIST_SIGNING_THUMBPRINT);
$r=Set-AuthenticodeSignature -LiteralPath $env:POLTERGEIST_SIGNATURE_FILE -Certificate $c -HashAlgorithm SHA256 -TimestampServer $env:POLTERGEIST_TIMESTAMP_URL;
if ($r.Status -ne 'Valid') { throw 'Signing failed' }
"""
    powershell(script, variables={"POLTERGEIST_SIGNING_THUMBPRINT": inputs["thumbprint"],
        "POLTERGEIST_SIGNATURE_FILE": str(output.absolute()), "POLTERGEIST_TIMESTAMP_URL": timestamp_url})
    verify_signature(output, [publisher])
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--certificate-thumbprint", required=True)
    parser.add_argument("--publisher-sha256", required=True)
    parser.add_argument("--timestamp-url", required=True)
    parser.add_argument("--installer-url")
    parser.add_argument("--rollout-percent", type=int, default=100)
    parser.add_argument("--rollout-salt", default="")
    args = parser.parse_args(argv)
    print(signed_metadata(args.installer, args.bundle_sha256, args.output, version=args.version,
        thumbprint=args.certificate_thumbprint, timestamp_url=args.timestamp_url, publisher=args.publisher_sha256,
        installer_url=args.installer_url, rollout_percent=args.rollout_percent, rollout_salt=args.rollout_salt))


if __name__ == "__main__":
    raise SystemExit(main())
