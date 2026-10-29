"""Create timestamped signed release metadata using a Windows store certificate."""

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from companion.signatures import powershell, verify_signature
from companion.updates import ReleaseManifest, file_digest


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


def signed_metadata(installer, bundle_digest, output, *, version, thumbprint, timestamp_url, publisher,
                    installer_url=None, rollout_percent=100, rollout_salt=""):
    inputs = signing_inputs("signtool.exe", thumbprint, timestamp_url, publisher, allow_http=True)
    if urlsplit(timestamp_url).scheme != "http":
        raise ValueError("Windows PowerShell 5.1 metadata signing requires an http:// timestamp service")
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
