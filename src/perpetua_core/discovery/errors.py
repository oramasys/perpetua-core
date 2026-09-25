class BackendOfflineError(RuntimeError):
    """Raised when an explicitly requested backend is not online."""


class NoBackendAvailableError(RuntimeError):
    """No registered backend satisfies the given routing constraints."""
