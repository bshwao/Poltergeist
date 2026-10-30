"""Typed, revision-checked workspace documents in the application's transaction."""

from sqlalchemy import select, update, delete, func

from backend.models import ExperimentDocumentRecord
from backend.persistence_errors import ConflictError, NotFoundError, PersistenceError
from backend.repositories import _flush, _id, _page, _revision, _timestamp
from shared.models.experimentation import DiscoveryPlan, SavedFeedback, SavedSuite
from shared.models.lab import LabReceipt

_MODELS = {"suite": SavedSuite, "discovery": DiscoveryPlan, "feedback": SavedFeedback, "receipt": LabReceipt}


class ExperimentRepository:
    def __init__(self, session, kind, *, owner_scope=None):
        self.session, self.kind = session, kind
        self.owner_scope = owner_scope
        self.model = _MODELS[kind]

    def _stored(self, row):
        value = self.model.model_validate_json(row.payload)
        if (str(value.id) != row.id or getattr(value, "revision", 1) != row.revision
                or _timestamp(value.created_at) != row.created_at or row.kind != self.kind):
            raise PersistenceError("Stored experiment document is inconsistent")
        return value

    def get(self, identity):
        query = select(ExperimentDocumentRecord).where(
            ExperimentDocumentRecord.id == _id(identity), ExperimentDocumentRecord.kind == self.kind,
        )
        if self.owner_scope is not None:
            query = query.where(ExperimentDocumentRecord.owner_scope == self.owner_scope)
        row = self.session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise NotFoundError("Experiment document does not exist")
        return self._stored(row)

    def create(self, value):
        value = self.model.model_validate(value.model_dump(mode="python"))
        if self.session.scalar(select(func.count()).select_from(ExperimentDocumentRecord).where(
                ExperimentDocumentRecord.owner_scope == self.owner_scope)) >= 10000:
            raise ConflictError("This workspace reached its experiment-document storage limit")
        row = ExperimentDocumentRecord(id=str(value.id), kind=self.kind, payload=value.model_dump_json(),
            owner_scope=self.owner_scope,
            revision=getattr(value, "revision", 1), created_at=_timestamp(value.created_at),
            updated_at=_timestamp(getattr(value, "updated_at", value.created_at)))
        self.session.add(row)
        _flush(self.session)
        return self.get(value.id)

    def replace(self, value, expected_revision):
        _revision(expected_revision)
        value = self.model.model_validate(value.model_dump(mode="python"))
        if value.revision != expected_revision + 1:
            raise PersistenceError("A document update must advance its revision once")
        self.get(value.id)
        result = self.session.execute(update(ExperimentDocumentRecord).where(
            ExperimentDocumentRecord.id == str(value.id), ExperimentDocumentRecord.kind == self.kind,
            ExperimentDocumentRecord.revision == expected_revision,
        ).values(payload=value.model_dump_json(), revision=value.revision,
                 updated_at=_timestamp(getattr(value, "updated_at", value.created_at))))
        if result.rowcount != 1:
            raise ConflictError("Experiment document changed; reload before updating")
        _flush(self.session)
        return self.get(value.id)

    def list(self, limit=50, offset=0, *, created_before=None):
        _page(limit, offset)
        rows = self.session.scalars(select(ExperimentDocumentRecord).where(
            ExperimentDocumentRecord.kind == self.kind,
            *([ExperimentDocumentRecord.owner_scope == self.owner_scope] if self.owner_scope is not None else []),
            *([ExperimentDocumentRecord.created_at <= _timestamp(created_before)] if created_before is not None else []),
        ).order_by(ExperimentDocumentRecord.created_at.desc(), ExperimentDocumentRecord.id.desc()).limit(limit).offset(offset))
        return [self._stored(row) for row in rows]

    def delete(self, identity, expected_revision):
        _revision(expected_revision)
        self.get(identity)
        result = self.session.execute(delete(ExperimentDocumentRecord).where(
            ExperimentDocumentRecord.id == _id(identity), ExperimentDocumentRecord.kind == self.kind,
            ExperimentDocumentRecord.revision == expected_revision,
        ))
        if result.rowcount != 1:
            raise ConflictError("Experiment document changed; reload before deleting")
        _flush(self.session)
