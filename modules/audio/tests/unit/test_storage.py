"""The recording is gone afterwards. Every afterwards.

Invariant 11 says raw audio does not survive the task. A test that only covers
the happy path proves the easy half: the paths that matter are the ones where
something went wrong, because those are the ones a `finally` exists for.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from autune_audio.config import AudioSettings
from autune_audio.storage import (
    RecordingTooLargeError,
    _reject_persistent,
    adopt,
    recording_on_disk,
)
from autune_core.errors import PrivacyViolationError


@pytest.fixture
def settings(tmp_path: Path) -> AudioSettings:
    return AudioSettings(temp_dir=str(tmp_path / "scratch"))


def test_the_recording_is_deleted_on_success(settings: AudioSettings) -> None:
    with recording_on_disk(io.BytesIO(b"audio"), settings=settings) as recording:
        assert recording.path.exists()
        held = recording.path

    assert not held.exists()
    assert recording.deleted is True


def test_the_recording_is_deleted_when_the_body_raises(settings: AudioSettings) -> None:
    """The case the finally exists for. A transcription failure is not rare."""
    held: Path | None = None
    with (
        pytest.raises(RuntimeError, match="whisper fell over"),
        recording_on_disk(io.BytesIO(b"audio"), settings=settings) as recording,
    ):
        held = recording.path
        raise RuntimeError("whisper fell over")

    assert held is not None
    assert not held.exists()


def test_the_recording_is_deleted_on_cancellation(settings: AudioSettings) -> None:
    """A Celery hard time limit arrives as BaseException, not Exception.

    ``except Exception`` would not see it, and the recording would be left on
    disk exactly when the worker is being killed.
    """
    held: Path | None = None
    with (
        pytest.raises(KeyboardInterrupt),
        recording_on_disk(io.BytesIO(b"audio"), settings=settings) as recording,
    ):
        held = recording.path
        raise KeyboardInterrupt

    assert held is not None
    assert not held.exists()


def test_the_original_exception_survives_a_failed_deletion(settings: AudioSettings) -> None:
    """Deletion failure must not mask why the task failed.

    Both facts have to reach the operator: the task broke, and the audio is
    still there. Replacing the first with the second loses the reason.
    """
    with (
        pytest.raises(RuntimeError, match="whisper fell over"),
        recording_on_disk(io.BytesIO(b"audio"), settings=settings) as recording,
    ):
        recording.path.unlink()
        recording.path.mkdir()  # a directory of the same name: unlink fails
        raise RuntimeError("whisper fell over")

    assert recording.deleted is False


def test_a_failed_deletion_raises_when_nothing_else_is(settings: AudioSettings) -> None:
    """A recording we could not delete is an incident, not a warning."""
    with (
        pytest.raises(PrivacyViolationError),
        recording_on_disk(io.BytesIO(b"audio"), settings=settings) as recording,
    ):
        recording.path.unlink()
        recording.path.mkdir()


def test_deleted_is_read_from_the_filesystem_not_from_reaching_a_line(
    settings: AudioSettings,
) -> None:
    """``original_audio_deleted`` is a claim made to four other modules.

    It is set from this flag, so this flag is confirmed by looking rather than
    by having called unlink.
    """
    with recording_on_disk(io.BytesIO(b"audio"), settings=settings) as recording:
        assert recording.deleted is False  # still on disk inside the block
    assert recording.deleted is True


def test_the_body_is_written_whole(settings: AudioSettings) -> None:
    payload = b"x" * (3 * 1024 * 1024 + 7)  # spans several chunk reads
    with recording_on_disk(io.BytesIO(payload), settings=settings) as recording:
        assert recording.path.read_bytes() == payload
        assert recording.bytes_written == len(payload)


class TestRefusesToWriteWhereAFileWouldSurvive:
    """The config docstring said "never point this at a synced folder".

    It now says so by failing. A synced directory keeps a copy after the unlink,
    in a second place nobody thought about, which is what invariant 11 means by
    "never copied into a temp path that survives the task".
    """

    @pytest.mark.parametrize(
        "directory",
        [
            "~/Dropbox/autune-tmp",
            "~/Library/Mobile Documents/com~apple~CloudDocs/tmp",
            "~/OneDrive/scratch",
            "~/Google Drive/tmp",
        ],
    )
    def test_a_synced_directory_is_refused(self, directory: str) -> None:
        with (
            pytest.raises(PrivacyViolationError, match="deleted|copies"),
            recording_on_disk(io.BytesIO(b"audio"), settings=AudioSettings(temp_dir=directory)),
        ):
            pass  # pragma: no cover - the context manager raises on entry

    def test_a_directory_inside_the_checkout_is_refused(self) -> None:
        """Where a recording can be committed."""
        inside = Path(__file__).resolve().parents[4] / "tmp-audio"
        with (
            pytest.raises(PrivacyViolationError, match="checkout"),
            recording_on_disk(io.BytesIO(b"audio"), settings=AudioSettings(temp_dir=str(inside))),
        ):
            pass  # pragma: no cover - the context manager raises on entry


class TestAdoptingAFileSomebodyElseWrote:
    """The worker is handed a path, not a stream.

    Wrapping that path in ``recording_on_disk`` would copy it and delete the
    copy, leaving the original — the durable copy invariant 11 is actually
    about. The leak would look fixed.
    """

    def test_the_adopted_file_is_the_one_deleted(self, tmp_path: Path) -> None:
        upload = tmp_path / "meeting.m4a"
        upload.write_bytes(b"audio")

        with adopt(upload) as recording:
            assert recording.path == upload

        assert not upload.exists()
        assert recording.deleted is True

    def test_it_is_deleted_when_the_body_raises(self, tmp_path: Path) -> None:
        upload = tmp_path / "meeting.m4a"
        upload.write_bytes(b"audio")

        with pytest.raises(RuntimeError), adopt(upload):
            raise RuntimeError("pipeline fell over")

        assert not upload.exists()

    def test_a_synced_location_is_deleted_rather_than_refused(self, tmp_path: Path) -> None:
        """Refusing here would decline to delete a file that is already there.

        ``recording_on_disk`` rejects a bad directory before writing anything.
        By the time a file has been adopted the choice is already made, and
        deleting it is strictly better than leaving it.
        """
        synced = tmp_path / "Dropbox (Acme Inc)"
        synced.mkdir()
        upload = synced / "meeting.m4a"
        upload.write_bytes(b"audio")

        with adopt(upload):
            pass

        assert not upload.exists()


class TestTheSizeLimit:
    def test_an_overlong_stream_stops_and_leaves_nothing_behind(
        self, settings: AudioSettings
    ) -> None:
        """Counted while writing, because a declared size is a client's claim."""
        held: Path | None = None
        with (
            pytest.raises(RecordingTooLargeError),
            recording_on_disk(
                io.BytesIO(b"x" * 4096), max_bytes=1024, settings=settings
            ) as recording,
        ):
            held = recording.path  # pragma: no cover - the write raises first

        assert held is None or not held.exists()
        assert not any(Path(settings.temp_dir).iterdir())

    def test_a_stream_within_the_limit_is_untouched(self, settings: AudioSettings) -> None:
        with recording_on_disk(io.BytesIO(b"x" * 512), max_bytes=1024, settings=settings) as rec:
            assert rec.path.read_bytes() == b"x" * 512


class TestSyncedFolderNamesAsTheyActuallyAppear:
    """A sync client does not name its folder exactly "Dropbox".

    An exact-segment check waved through the configuration a work laptop
    arrives in, which is the one that matters most.
    """

    @pytest.mark.parametrize(
        "directory",
        [
            "~/OneDrive - Acme Corp/tmp",
            "~/Dropbox (Acme Inc)/scratch",
            "~/Library/Mobile Documents/com~apple~CloudDocs/tmp",
            "~/Google Drive/My Drive/tmp",
            "~/Nextcloud/tmp",
        ],
    )
    def test_a_suffixed_sync_folder_is_still_refused(self, directory: str) -> None:
        with (
            pytest.raises(PrivacyViolationError, match="copies"),
            recording_on_disk(io.BytesIO(b"audio"), settings=AudioSettings(temp_dir=directory)),
        ):
            pass  # pragma: no cover - the context manager raises on entry

    def test_a_name_that_merely_contains_one_is_allowed(self) -> None:
        """The check refuses synced directories, not directories named like one.

        ``/tmp/my-dropbox-cache`` is local. Refusing it would be a false
        positive, and false positives teach people to work around the check.
        """
        _reject_persistent(Path("/tmp/my-dropbox-cache"))
        _reject_persistent(Path("/var/onedrive-backups"))

        with pytest.raises(PrivacyViolationError):
            _reject_persistent(Path("/Users/x/Dropbox (Acme)/tmp"))
