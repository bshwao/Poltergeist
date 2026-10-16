"""Public persistence errors, independent of a database driver."""


class PersistenceError(ValueError):
    """A persistence operation cannot satisfy the requested invariant."""


class NotFoundError(PersistenceError):
    """The requested stored record does not exist."""


class ConflictError(PersistenceError):
    """A duplicate, stale revision, or referenced record conflicts with a write."""


class DatabaseInitializationError(PersistenceError):
    """Database history or schema is incompatible with this application."""
