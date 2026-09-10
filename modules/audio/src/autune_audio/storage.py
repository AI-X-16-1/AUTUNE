"""A recording exists on disk only inside a ``with`` block.

Invariant 11's first clause: raw audio is deleted immediately after
transcription, is never persisted to durable storage, never logged, and never
copied into a temp path that survives the task.

The rule is easy to state and easy to half-do. A ``finally`` in one place does
not help the next caller, and "delete it afterwards" is a step someone forgets
the way they forget any step. So there is one primitive here and every path that
puts a recording on disk goes through it — the worker task and the dev upload
page alike.

It also refuses to write somewhere the file would outlive the block. A directory
inside a cloud-sync folder or inside the checkout keeps a copy after the unlink,
in a second place nobody thought about; ``AudioSettings`` used to say so in a
docstring and now says so by failing.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO

from autune_core import get_logger
from autune_core.errors import AutuneError, PrivacyViolationError

from .config import AudioSettings, get_settings

log = get_logger(__name__)

CHUNK_BYTES = 1024 * 1024


class RecordingTooLargeError(AutuneError):
    """The stream ran past ``max_bytes``. The partial file is deleted anyway."""

    code = "recording_too_large"
    status_code = 413

    def __init__(self, limit_bytes: int) -> None:
        self.limit_mb = limit_bytes // 1024 // 1024
        super().__init__(f"recording exceeds {self.limit_mb}MB")


@dataclass
class Recording:
    """The path, and whether the file is gone.

    ``deleted`` starts False and becomes True only once the file is confirmed
    absent — not once ``unlink`` was called. It is what
    ``PrivacyFlags.original_audio_deleted`` is set from, and that flag is a claim
    to four other modules, so it is read from the filesystem rather than from
    the fact that the code reached a line.
    """

    path: Path
    deleted: bool = field(default=False, init=False)
    bytes_written: int = field(default=0, init=False)


# A path segment matches when it *is* one of these or begins with one followed
# by a space.
#
# Exact match alone is too strict: the names a sync client actually creates
# carry a suffix -- "OneDrive - Acme", "Dropbox (Acme Inc)" -- and waving those
# through means missing the configuration a work laptop arrives in.
#
# A bare prefix is too loose in the other direction: "onedrive-backups" and
# "my-dropbox-cache" are local directories, and refusing them is a false
# positive. False positives are not free here -- they teach people to work
# around the check, and the check is the whole point.
#
# The space is what separates the two. Every sync client's suffix begins with
# one; a local name joins with a hyphen or an underscore.
_SYNCED_SEGMENTS = (
    "dropbox",
    "onedrive",
    "google drive",
    "googledrive",
    "icloud drive",
    "mobile documents",  # ~/Library/Mobile Documents: iCloud Drive on macOS
    "nextcloud",
    "sync.com",
)


def _reject_persistent(directory: Path) -> None:
    """Refuse a directory a recording could survive in.

    Named rather than checked inline because the reason has to travel with the
    error: someone points ``AUTUNE_AUDIO_TEMP_DIR`` at a folder they can browse
    while debugging, and the failure has to say why that is not a small thing.
    """
    resolved = directory.expanduser().resolve()

    for part in resolved.parts:
        lowered = part.lower()
        hit = next(
            (p for p in _SYNCED_SEGMENTS if lowered == p or lowered.startswith(f"{p} ")),
            None,
        )
        if hit is not None:
            raise PrivacyViolationError(
                f"AUTUNE_AUDIO_TEMP_DIR passes through {part!r}, which copies the "
                "recording off this machine before it is deleted. Point it at a "
                "local scratch directory. See docs/architecture/privacy.md section 1."
            )

    repo = Path(__file__).resolve().parents[4]
    if resolved == repo or repo in resolved.parents:
        raise PrivacyViolationError(
            f"AUTUNE_AUDIO_TEMP_DIR ({resolved}) is inside the checkout, where a "
            "recording can be committed. Point it outside the repository. "
            "See docs/architecture/privacy.md section 1."
        )


@contextmanager
def adopt(path: Path) -> Iterator[Recording]:
    """Take ownership of a recording already on disk and delete it.

    The worker is handed a path by the upload endpoint rather than a stream, and
    that file is the durable copy invariant 11 cares about. Passing it through
    ``recording_on_disk`` would copy it, delete the copy, and leave the original
    exactly where it was — so the worker adopts it instead.

    No directory check here. The location was chosen by whoever wrote the file,
    and refusing it would mean declining to delete a recording that is already
    on disk, which is strictly worse than deleting it.
    """
    recording = Recording(path=path)
    try:
        yield recording
    finally:
        failure = _delete(recording)
        if failure is not None and sys.exc_info()[0] is None:
            raise failure


@contextmanager
def recording_on_disk(
    stream: IO[bytes],
    *,
    suffix: str = "",
    max_bytes: int | None = None,
    settings: AudioSettings | None = None,
) -> Iterator[Recording]:
    """Write ``stream`` to a temp file, yield it, and delete it whatever happens.

    ``finally`` rather than a normal return path, so success, an exception and a
    cancellation all delete. ``BaseException`` is covered too — a Celery hard
    time limit arrives as one, and it is exactly the case where a recording
    would otherwise be left behind.

    Deletion failure is reported, never swallowed. If nothing else is already
    propagating, it raises: a recording we could not delete is a privacy
    incident, not a warning. If an exception *is* propagating, the original one
    wins — masking it would lose the reason the task failed — and ``deleted``
    stays False so nothing downstream can claim the audio is gone.

    ``max_bytes`` stops a stream that runs over, and the partial file is deleted
    by the same ``finally`` as any other. The limit belongs here rather than in
    the caller because here is where the bytes reach disk: a caller that checks
    a declared size first is trusting a number the client sent.
    """
    settings = settings or get_settings()
    directory = Path(settings.temp_dir)
    _reject_persistent(directory)
    directory.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(dir=directory, suffix=suffix, delete=False) as handle:
        recording = Recording(path=Path(handle.name))

    try:
        with recording.path.open("wb") as out:
            while chunk := stream.read(CHUNK_BYTES):
                recording.bytes_written += len(chunk)
                if max_bytes is not None and recording.bytes_written > max_bytes:
                    raise RecordingTooLargeError(max_bytes)
                out.write(chunk)
        yield recording
    finally:
        failure = _delete(recording)
        # Only when nothing else is on its way out. Raising unconditionally here
        # would replace the in-flight exception with this one, which is the
        # opposite of letting the original win.
        if failure is not None and sys.exc_info()[0] is None:
            raise failure


def _delete(recording: Recording) -> PrivacyViolationError | None:
    """Unlink, confirm, and record the outcome. Returns what to raise, if any.

    Returning the error instead of raising lets the caller decide whether to
    raise it now or let an in-flight exception through first, without this
    function needing to know which.
    """
    try:
        recording.path.unlink(missing_ok=True)
    except OSError as exc:
        log.error("audio_delete_failed", error=type(exc).__name__)
        return PrivacyViolationError(
            "the recording could not be deleted; refusing to continue as though "
            "it were. See docs/architecture/privacy.md section 1."
        )

    if recording.path.exists():
        log.error("audio_delete_incomplete")
        return PrivacyViolationError(
            "the recording is still on disk after unlink; refusing to continue "
            "as though it were deleted."
        )

    recording.deleted = True
    # Size only. A filename can carry a meeting title and a title is content.
    log.info("audio_deleted", bytes=recording.bytes_written)
    return None
