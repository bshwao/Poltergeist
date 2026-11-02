"""Match frozen registered bytes to full immutable Git objects, without checkout."""

import base64
from hashlib import sha1, sha256
import json
import re

from backend.services.github_provider import GitHubError
from shared.models.github import CommitProof, repo_path
from simulator.agents.fingerprints import _EXCLUDED_DIRECTORIES

SHA = re.compile(r"^[0-9a-f]{40}$")


class CommitVerifier:
    def __init__(self, remote, repository):
        self.remote, self.repository = remote, repository
        self.trees, self.blobs = {}, {}
        self.decoded_bytes = 0

    def commit_tree(self, commit):
        value = self.remote.commit(self.repository, commit)
        tree = value.get("tree", {}).get("sha") if isinstance(value.get("tree"), dict) else None
        if value.get("sha") != commit or not isinstance(tree, str) or not SHA.fullmatch(tree):
            raise GitHubError("GitHub did not confirm the exact full commit and tree")
        return tree

    def entries(self, tree):
        if tree not in self.trees:
            value = self.remote.tree(self.repository, tree)
            items = value.get("tree")
            if value.get("sha") != tree or value.get("truncated") is not False or not isinstance(items, list) or len(items) > 20000:
                raise GitHubError("The committed tree is invalid, truncated or too large to verify")
            result = {}
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str) or "/" in item["path"]:
                    raise GitHubError("GitHub returned an invalid direct tree entry")
                name = repo_path(item["path"])
                if name in result or not isinstance(item.get("sha"), str) or not SHA.fullmatch(item["sha"]):
                    raise GitHubError("GitHub returned duplicate or invalid tree identities")
                result[name] = item
            self.trees[tree] = result
        return self.trees[tree]

    def path(self, root, path):
        current = root
        parts = repo_path(path).split("/")
        for index, part in enumerate(parts):
            entry = self.entries(current).get(part)
            if entry is None:
                raise GitHubError("A frozen input is absent from the selected commit")
            if index == len(parts) - 1:
                return entry
            if entry.get("type") != "tree" or entry.get("mode") != "040000":
                raise GitHubError("Repository paths must not traverse links or submodules")
            current = entry["sha"]

    def inventory(self, tree, prefix="", depth=0):
        if depth > 20:
            raise GitHubError("Committed package exceeds the directory-depth limit")
        result = {}
        for name, entry in self.entries(tree).items():
            if entry.get("type") == "tree" and entry.get("mode") == "040000":
                if name not in _EXCLUDED_DIRECTORIES:
                    result.update(self.inventory(entry["sha"], prefix + name + "/", depth + 1))
            elif entry.get("type") == "blob" and entry.get("mode") in {"100644", "100755"}:
                result[prefix + name] = entry
            else:
                raise GitHubError("Committed packages containing links/submodules are not supported")
            if len(result) > 256:
                raise GitHubError("Committed package exceeds the 256-file verification limit")
        return result

    def data(self, entry):
        sha = entry["sha"]
        if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
            raise GitHubError("Model pins must be ordinary committed files")
        if type(entry.get("size")) is not int or not 0 <= entry["size"] <= 1024**2:
            raise GitHubError("Use a committed LFS pointer or model pin for large weights")
        if sha not in self.blobs:
            value = self.remote.blob(self.repository, sha)
            try:
                if value.get("encoding") != "base64" or value.get("sha") != sha or not isinstance(value.get("content"), str):
                    raise ValueError
                data = base64.b64decode(value["content"].replace("\n", ""), validate=True)
                if len(data) != entry["size"] or type(value.get("size")) is not int or value["size"] != len(data):
                    raise ValueError
                if sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != sha:
                    raise ValueError
            except (ValueError, TypeError):
                raise GitHubError("Committed blob content failed identity verification") from None
            self.decoded_bytes += len(data)
            if self.decoded_bytes > 8 * 1024**2:
                raise GitHubError("Frozen-input verification exceeded its total byte budget")
            self.blobs[sha] = data
        return self.blobs[sha]

    def match(self, entry, fingerprint, *, external=False):
        data = self.data(entry)
        if len(data) == fingerprint.size_bytes and sha256(data).hexdigest() == fingerprint.sha256:
            return "raw_files"
        # Git for Windows may materialize text with CRLF. Accept only this
        # exact declared byte transformation, not arbitrary whitespace changes.
        if not external and b"\0" not in data:
            try:
                data.decode("utf-8")
                crlf = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                if len(crlf) == fingerprint.size_bytes and sha256(crlf).hexdigest() == fingerprint.sha256:
                    return "crlf_files"
            except UnicodeError:
                pass
        pointer = re.fullmatch(rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?", data.replace(b"\r\n", b"\n"))
        if pointer and pointer[1].decode() == fingerprint.sha256 and int(pointer[2]) == fingerprint.size_bytes:
            return "lfs_pins"
        if external:
            try:
                def unique_object(pairs):
                    result = dict(pairs)
                    if len(result) != len(pairs):
                        raise ValueError("Duplicate model-pin fields")
                    return result
                pin = json.loads(data, object_pairs_hook=unique_object)
                if (isinstance(pin, dict) and set(pin) == {"sha256", "size_bytes"}
                        and pin["sha256"] == fingerprint.sha256 and type(pin["size_bytes"]) is int and pin["size_bytes"] == fingerprint.size_bytes):
                    return "manifest_pins"
            except (ValueError, TypeError, UnicodeError):
                pass
        raise GitHubError("The selected commit does not match a frozen package file or declared model pin")

    def verify(self, binding, snapshot):
        if len(snapshot.files) + len(snapshot.model_files) > 256:
            raise GitHubError("At most 256 total frozen package/model inputs are supported per controller")
        if len(binding.model_paths) != len(snapshot.model_files):
            raise GitHubError(f"This frozen controller needs exactly {len(snapshot.model_files)} committed external-model paths, in the displayed model order")
        root = self.commit_tree(binding.commit)
        package = root
        if binding.package_prefix:
            entry = self.path(root, binding.package_prefix)
            if entry.get("type") != "tree" or entry.get("mode") != "040000":
                raise GitHubError("The package prefix must identify a committed directory")
            package = entry["sha"]
        inventory = self.inventory(package)
        if set(inventory) != {item.path for item in snapshot.files}:
            raise GitHubError("Committed package inventory differs from the frozen registration")
        counts = dict(raw_files=0, crlf_files=0, lfs_pins=0, manifest_pins=0)
        proof = []
        for item in snapshot.files:
            entry = inventory[item.path]
            method = self.match(entry, item)
            counts[method] += 1
            proof.append((item.path, entry["sha"], item.sha256, item.size_bytes, method))
        for path, item in zip(binding.model_paths, snapshot.model_files, strict=True):
            entry = self.path(root, path)
            method = self.match(entry, item, external=True)
            counts[method] += 1
            proof.append((path, entry["sha"], item.sha256, item.size_bytes, method))
        return CommitProof(commit=binding.commit, tree=root, package_prefix=binding.package_prefix,
            package_files=len(snapshot.files), external_models=len(snapshot.model_files), **counts,
            inventory_digest=sha256(json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
