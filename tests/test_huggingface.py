"""Pinned provider metadata, explicit account storage and bearer boundaries."""
from dataclasses import replace
from io import BytesIO
import json
import urllib.error
import urllib.request

import pytest

from companion.credential_store import WindowsCredentialStore
from companion.huggingface import HuggingFaceAccount, HuggingFaceSource, PLANT_HUB, _HubRedirects
from companion import huggingface
from companion.model_assets import AssetError, DownloadPaused, PLANT_WEIGHTS


TOKEN = "hf_" + "private_fixture_token_only" * 2


class Metadata(BytesIO):
    status = 200
    headers = {}
    def geturl(self): return PLANT_HUB.metadata_url


def metadata():
    return {"sha": PLANT_HUB.revision, "cardData": {"license": "apache-2.0"}, "gated": False,
        "siblings": [{"rfilename": "epoch=029_final_1.ckpt", "size": 446559352,
            "lfs": {"sha256": "2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4", "size": 446559352}}]}


def source(value, account=None):
    return HuggingFaceSource(account=account, _open=lambda *a, **k: Metadata(json.dumps(value).encode()))


def test_curated_pin_reuses_existing_verified_cache_and_requires_immutable_revisions():
    assert PLANT_HUB.asset() == PLANT_WEIGHTS
    assert source(metadata()).verify_source()["verified"]
    with pytest.raises(ValueError, match="immutable"):
        replace(PLANT_HUB, revision="main")
    for filename in ("../weights.ckpt", "a/../weights.ckpt", "/absolute.ckpt", "x\\weights.ckpt", "x//weights.ckpt"):
        with pytest.raises(ValueError): replace(PLANT_HUB, filename=filename)


@pytest.mark.parametrize("change", [lambda v: v.update(sha="a" * 40),
    lambda v: v["cardData"].update(license="unknown"), lambda v: v["siblings"].clear(),
    lambda v: v["siblings"].append(dict(v["siblings"][0])), lambda v: v["siblings"][0].update(size=1),
    lambda v: v["siblings"][0]["lfs"].update(sha256="0" * 64),
    lambda v: v["siblings"][0]["lfs"].update(size=True)])
def test_remote_metadata_cannot_change_the_catalogue_pin_or_license(change):
    value = metadata()
    change(value)
    with pytest.raises(AssetError, match="No model pin was changed"):
        source(value).verify_source()
    assert PLANT_HUB.asset() == PLANT_WEIGHTS


def test_ambient_hub_tokens_and_login_cache_are_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", TOKEN)
    requests = []
    def open_request(request, **kwargs):
        requests.append(request)
        return Metadata(json.dumps(metadata()).encode())
    account = HuggingFaceAccount(tmp_path)
    provider = HuggingFaceSource(account=account, _open=open_request)
    provider.verify_source()
    assert all(not request.has_header("Authorization") for request in requests)
    assert account.status()["configured"] is False
    assert not (tmp_path / "providers").exists()


def test_explicit_account_is_protected_separately_and_clear_preserves_enrollment(tmp_path):
    # Test protection primitives exercise the real atomic framing and binding.
    store = WindowsCredentialStore(tmp_path / "providers" / "huggingface",
        _protect=lambda data, entropy: bytes(value ^ 0xA5 for value in data),
        _unprotect=lambda data, entropy: bytes(value ^ 0xA5 for value in data))
    enrollment = tmp_path / "remote-enrollment.bin"
    enrollment.write_bytes(b"existing enrollment sentinel")
    account = HuggingFaceAccount(tmp_path, _store=store)
    account.save(TOKEN)
    assert TOKEN.encode() not in store.path.read_bytes()
    assert account.status()["configured"] and "token" not in account.status()
    provider = HuggingFaceSource(account=account)
    request = provider._request(PLANT_HUB.url)
    assert request.unredirected_hdrs["Authorization"] == "Bearer " + TOKEN
    assert "Authorization" not in request.headers
    redirected = _HubRedirects().redirect_request(request, None, 302, "", {}, "https://cdn-lfs.huggingface.co/signed-file")
    assert not redirected.has_header("Authorization")
    second = _HubRedirects().redirect_request(redirected, None, 302, "", {}, "https://other.invalid/signed-file")
    assert not second.has_header("Authorization")
    account.clear()
    assert account.status()["configured"] is False and enrollment.read_bytes() == b"existing enrollment sentinel"


@pytest.mark.parametrize("url", ["http://huggingface.co/x", "https://huggingface.co.other.invalid/x",
    "https://huggingface.co:444/x", "https://user@huggingface.co/x", "https://other.invalid/x"])
def test_credentials_are_never_attached_to_a_different_origin(url):
    with pytest.raises(AssetError, match="official HTTPS Hub origin"):
        HuggingFaceSource()._request(url)


def test_access_errors_do_not_expose_tokens_or_signed_urls(tmp_path):
    account = HuggingFaceAccount(tmp_path)
    def refused(*a, **k):
        raise urllib.error.HTTPError("https://huggingface.co/private?signature=secret_sentinel", 403, TOKEN, {}, BytesIO())
    with pytest.raises(AssetError, match="access was refused") as failure:
        HuggingFaceSource(account=account, _open=refused).verify_source()
    assert TOKEN not in str(failure.value) and "secret_sentinel" not in str(failure.value)


def test_metadata_read_is_bounded_and_pause_prevents_network(monkeypatch):
    monkeypatch.setattr(huggingface, "MAX_METADATA_BYTES", 64)
    read_bytes = []
    class Counted(Metadata):
        def read1(self, size=-1):
            value = super().read1(size)
            read_bytes.append(len(value))
            return value
    response = Counted(b"x" * 200)
    with pytest.raises(AssetError):
        HuggingFaceSource(_open=lambda *a, **k: response).verify_source()
    assert sum(read_bytes) <= 65
    with pytest.raises(DownloadPaused):
        HuggingFaceSource(_open=lambda *a, **k: pytest.fail("Paused request contacted the Hub")).verify_source(cancelled=lambda: True)


def test_download_only_accepts_the_curated_url_and_drops_unrelated_headers():
    seen = []
    def open_request(request, **kwargs):
        seen.append(request)
        return Metadata(b"")
    provider = HuggingFaceSource(_open=open_request)
    with pytest.raises(AssetError, match="pinned download"):
        provider.open_asset(urllib.request.Request("https://other.invalid/weights"))
    request = urllib.request.Request(PLANT_HUB.url, headers={"Range": "bytes=100-", "Authorization": TOKEN, "Cookie": "ambient_cookie"})
    provider.open_asset(request).close()
    assert seen[0].get_header("Range") == "bytes=100-"
    assert not seen[0].has_header("Authorization") and not seen[0].has_header("Cookie")
