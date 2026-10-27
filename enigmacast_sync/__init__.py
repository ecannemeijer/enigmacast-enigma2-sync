"""EnigmaCast sync server for Enigma2."""

__version__ = "0.3.3"
# Reported by GET /api/v1/health and GET /api/v1/version so the app can offer an update.
SERVER_VERSION = __version__

# Per-thread stack for the request threads. The default (8 MiB on most receivers) is mostly
# untouched address space, but a smaller reservation keeps the servers lean on boxes with
# ~25 MB free. TLS, sqlite and json need far less than this.
THREAD_STACK_BYTES = 256 * 1024


def small_thread_stacks() -> None:
    """Call once before the server starts its threads. Ignored where the platform refuses."""
    import threading
    try:
        threading.stack_size(THREAD_STACK_BYTES)
    except (ValueError, RuntimeError):
        pass
