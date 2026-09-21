"""A recording exists on disk only inside a ``with`` block.

Invariant 11's first clause: raw audio is deleted immediately after
transcription, is never persisted to durable storage, never logged, and never
copied into a temp path that survives the task.

The rule is easy to state and easy to half-do. A ``finally`` in one place does
not help the next caller, and "delete it afterwards" is a step someone forgets
the way they forget any step. So no caller opens a file itself: every path that
puts a recording on disk goes through a primitive here, and each one names who
deletes it.

- ``recording_on_disk`` — writes and deletes in the same block. The dev upload
  page, and anything else that transcribes in one process.
- ``handover`` — writes, and deletes only if the block fails. The upload
  endpoint, which must leave the file for a worker in another process.
- ``adopt`` — takes a file someone else wrote and deletes it. The worker end of
  a ``handover``.

The two ends never exchange a path. The endpoint renames the file to
``{job_id}.upload`` (``assign``) and queues the job id; the worker rebuilds the
same path from the id (``upload_path``). privacy.md section 1 forbids a path to
raw audio in a Celery payload, and this is how the handover keeps that rule
rather than reads around it (#275).

Exactly one of them owns a given file at a time, which is the property that
matters: a recording with two owners gets deleted twice and a recording with
none never gets deleted at all.

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

UPLOAD_SUFFIX = ".upload"
"""The one suffix an uploaded recording is stored under.

Not the client's extension: ffmpeg sniffs the container from the bytes, and a
name that repeats what the client sent is the NAME_MAX crash from #209's
review waiting to happen again. The stem is the job id and nothing else.
"""


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
# The space is what separates the two on Windows and in a home directory. Every
# sync client's suffix there begins with one; a local name joins with a hyphen
# or an underscore. It is not true under ~/Library/CloudStorage, which is why
# that parent is matched on its own above.
_SYNCED_SEGMENTS = (
    # macOS 12.3+ puts every provider under ~/Library/CloudStorage, and names
    # the folders <Provider>-<Account>: OneDrive-Personal,
    # GoogleDrive-user@gmail.com, Box-Box. That inverts the space rule below --
    # on a Mac a hyphen is what a *sync* folder uses. Matching the parent
    # catches all of them, and catches providers not listed here at all.
    "cloudstorage",
    # Google Drive for desktop mounted as a drive letter: the top level is
    # G:\My Drive and the provider's name is nowhere in the path.
    "my drive",
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


def upload_path(job_id: str, settings: AudioSettings | None = None) -> Path:
    """Where the recording for ``job_id`` is, on both sides of the handover.

    The worker calls this with the id it was queued with and gets the file the
    endpoint left. One function, two callers, so the two cannot disagree on
    the name -- a disagreement would be a recording nobody deletes.
    """
    settings = settings or get_settings()
    return Path(settings.temp_dir) / f"{job_id}{UPLOAD_SUFFIX}"


def job_id_of(path: Path) -> str | None:
    """The job an upload file belongs to, or None if it is not one of ours."""
    if path.suffix != UPLOAD_SUFFIX:
        return None
    return path.stem


def assign(recording: Recording, job_id: str) -> None:
    """Give a handed-over recording the name the worker will look for.

    A rename inside the same directory: atomic on every filesystem we run on,
    and it cannot leave a second copy. ``recording.path`` is updated only
    once the rename succeeded, so a failure here leaves ``handover``'s
    ``except`` pointing at the file that actually exists.
    """
    target = recording.path.with_name(f"{job_id}{UPLOAD_SUFFIX}")
    recording.path.rename(target)
    recording.path = target


def delete_orphan(path: Path) -> None:
    """Delete a recording no task owns, and confirm it is gone.

    The sweep's primitive. Same ``_delete`` as every other path out of this
    module, so a deletion that did not happen raises here too rather than
    being counted as done.
    """
    failure = _delete(Recording(path=path))
    if failure is not None:
        raise failure


@contextmanager
def adopt(path: Path) -> Iterator[Recording]:
    """Take ownership of a recording already on disk and delete it.

    The worker is queued a job id, not a stream, and rebuilds the path with
    ``upload_path``; that file is the durable copy invariant 11 cares about.
    Passing it through ``recording_on_disk`` would copy it, delete the copy,
    and leave the original exactly where it was — so the worker adopts it
    instead.

    A missing file is not an error here: ``_delete`` uses ``missing_ok``, and
    a redelivered task whose first run already deleted the upload fails at
    decode with a message that says what is missing, not here.

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


def _new_temp_file(*, suffix: str, settings: AudioSettings | None) -> Recording:
    """Reserve a path for a recording, refusing anywhere it could survive.

    Shared by both context managers below. The directory check is the kind of
    guard that stops being a guard once there are two copies of it: one gets a
    new synced-folder name and the other does not, and which door the recording
    came through decides whether the rule applied.
    """
    settings = settings or get_settings()
    directory = Path(settings.temp_dir)
    _reject_persistent(directory)
    directory.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(dir=directory, suffix=suffix, delete=False) as handle:
        return Recording(path=Path(handle.name))


def _fill(recording: Recording, stream: IO[bytes], *, max_bytes: int | None) -> None:
    """Copy ``stream`` onto disk, stopping if it runs past ``max_bytes``.

    Counting as we write rather than trusting ``UploadFile.size``: that is a
    number the client sent, and it is ``None`` on a request with no
    Content-Length. The caller's ``finally`` or ``except`` deletes the partial
    file — that is not this function's job and it must not become it, or the
    deletion rule lives in two places.
    """
    with recording.path.open("wb") as out:
        while chunk := stream.read(CHUNK_BYTES):
            recording.bytes_written += len(chunk)
            if max_bytes is not None and recording.bytes_written > max_bytes:
                raise RecordingTooLargeError(max_bytes)
            out.write(chunk)


@contextmanager
def handover(
    stream: IO[bytes],
    *,
    max_bytes: int | None = None,
    settings: AudioSettings | None = None,
) -> Iterator[Recording]:
    """Write ``stream`` to a temp file and hand the file to the worker.

    The one primitive here that leaves a recording on disk, and it is not a hole
    in invariant 11. The upload endpoint and the worker are different processes:
    the file has to outlive the request that wrote it, or there is nothing for
    ``adopt`` to adopt. What invariant 11 actually forbids is a recording with
    *no* owner, and that is what this manages — ownership passes to the worker
    at the end of the block and at no other moment.

    So the asymmetry is deliberate. The block's body is the enqueue, and:

    - it returns → the task is queued, the worker will ``adopt`` the path, and
      deletion is that task's ``finally``. ``deleted`` stays False here because
      the file is still there, and saying otherwise would make
      ``PrivacyFlags.original_audio_deleted`` a lie four modules act on.
    - it raises → nobody is coming. The broker was unreachable, the meeting row
      would not commit, the client hung up. This deletes, because a recording
      whose task does not exist is a recording that never gets collected.

    ``BaseException``, not ``Exception``: a cancelled request arrives as one,
    and a cancelled upload is exactly when a file gets left behind.

    A deletion failure is logged by ``_delete`` and does not replace the
    exception on its way out — same rule as ``recording_on_disk``, for the same
    reason: losing why the enqueue failed would cost more than it buys.

    ``max_bytes`` is enforced while the bytes are written rather than from a
    declared size, and an over-long body is deleted by the same path as any
    other failure.

    The file is written under a throwaway name and takes its real one --
    ``{job_id}.upload`` -- through ``assign`` once the caller has a job. The
    body is on disk before the claim can be made (it streams in with the
    request), so the name cannot be known when the file is created.
    """
    recording = _new_temp_file(suffix="", settings=settings)

    try:
        _fill(recording, stream, max_bytes=max_bytes)
        yield recording
    except BaseException:
        _delete(recording)
        raise


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
    by the same ``finally`` as any other. The limit lives in ``_fill`` rather
    than in the caller because that is where the bytes reach disk: a caller that
    checks a declared size first is trusting a number the client sent.
    """
    recording = _new_temp_file(suffix=suffix, settings=settings)

    try:
        _fill(recording, stream, max_bytes=max_bytes)
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
