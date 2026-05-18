"""Voice store exception hierarchy.

Repository implementations raise these so that consumers (S-025 routers,
S-011 seed ingestion) can map them to error envelopes without coupling to
any specific backend.
"""

from __future__ import annotations


class VoiceStoreError(Exception):
    """Base class for all voice-store errors."""


class VoiceIdInvalidError(VoiceStoreError):
    """Raised when a voice id fails the ``^[a-z0-9_-]{1,64}$`` pattern.

    Why: NFR-SE-03 path-safety guarantees that no client-supplied path
    component reaches the filesystem. The id pattern is the seam where
    that guarantee is enforced.
    """


class VoiceNotFoundError(VoiceStoreError):
    """Raised when a lookup or mutation targets a missing voice id."""


class VoiceAlreadyExistsError(VoiceStoreError):
    """Raised when ``create`` is called with an id that already exists."""
