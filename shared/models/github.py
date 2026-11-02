"""Reviewable, commit-bound GitHub check publication without provider secrets."""

from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .base import ContractModel
from .analysis import RegressionPolicy

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitSHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Disclosure = Literal["metrics", "fingerprints", "run_ids", "scenarios"]
Conclusion = Literal["success", "failure", "action_required", "cancelled"]


def repo_path(value):
    if (len(value) > 500 or "\\" in value or ":" in value or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(ord(character) < 32 for character in value)):
        raise ValueError("Use a relative committed repository path without traversal or empty segments")
    return PurePosixPath(value).as_posix()


class CommitBinding(ContractModel):
    commit: GitSHA
    package_prefix: Annotated[str, Field(max_length=500)] = ""
    model_paths: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=256)

    @field_validator("package_prefix")
    @classmethod
    def prefix(cls, value):
        return repo_path(value) if value else value

    @field_validator("model_paths")
    @classmethod
    def paths(cls, value):
        return [repo_path(item) for item in value]


class GitHubPreviewRequest(ContractModel):
    request_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID
    candidate_index: Annotated[int, Field(ge=1, le=7, strict=True)] = 1
    policy: RegressionPolicy = Field(default_factory=RegressionPolicy)
    reference: CommitBinding
    candidate: CommitBinding
    disclose: list[Disclosure] = Field(default_factory=list, max_length=4)
    details_url: Annotated[str, Field(max_length=1000)] | None = None

    @field_validator("details_url")
    @classmethod
    def details(cls, value):
        if value is None:
            return value
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.port not in {None, 443}
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or any(character.isspace() or ord(character) < 32 for character in value)):
                raise ValueError
        except ValueError:
            raise ValueError("A shared report link must be HTTPS without credentials, query, fragment or a custom port") from None
        return value

    @model_validator(mode="after")
    def unique_disclosure(self) -> Self:
        if len(set(self.disclose)) != len(self.disclose):
            raise ValueError("Select each disclosure category once")
        return self


class CommitProof(ContractModel):
    commit: GitSHA
    tree: GitSHA
    package_prefix: str
    package_files: int
    external_models: int
    raw_files: int
    crlf_files: int
    lfs_pins: int
    manifest_pins: int
    inventory_digest: Digest


class GitHubApproval(ContractModel):
    digest: Digest
    approve: Literal[True]

    @field_validator("approve", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Publishing selected evidence requires explicit boolean approval")
        return value


class GitHubPreview(ContractModel):
    id: UUID
    operation_id: UUID
    repository: str
    app_id: int
    candidate_index: int
    policy: RegressionPolicy
    execution_status: str
    conclusion: Conclusion
    digest: Digest
    source_digest: Digest
    context_digest: Digest
    created_at: AwareDatetime
    expires_at: AwareDatetime
    reference: CommitProof
    candidate: CommitProof
    disclose: list[Disclosure]
    # Exact check-run JSON excluding its Authorization header.
    payload: str


class GitHubPublication(ContractModel):
    schema_version: Literal["poltergeist-github-check-v1"] = "poltergeist-github-check-v1"
    id: UUID
    preview: GitHubPreview
    state: Literal["preview", "publishing", "published", "failed", "uncertain"]
    message: str
    check_run_id: int | None = None
    check_url: str | None = None


class GitHubStatus(ContractModel):
    enabled: bool
    configured: bool
    repository: str
    app_id: int | None
    check_name: str
    daily_publish_limit: int
    scope: Literal["local_api_only"] = "local_api_only"


class GitHubModelInput(ContractModel):
    index: int
    sha256: Digest
    size_bytes: int


class GitHubControllerInputs(ContractModel):
    agent_index: int
    supported: bool
    package_path: str | None
    package_files: int
    models: list[GitHubModelInput] = Field(max_length=256)


class GitHubInputs(ContractModel):
    operation_id: UUID
    controllers: list[GitHubControllerInputs] = Field(max_length=8)


class GitHubContracts(ContractModel):
    request: GitHubPreviewRequest
    approval: GitHubApproval
    publication: GitHubPublication
    status: GitHubStatus
    inputs: GitHubInputs
