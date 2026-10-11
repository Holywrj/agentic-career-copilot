"""Real local filesystem behavior, including failures at publication boundaries."""

import asyncio
import os
import stat
import threading
from uuid import uuid4

import pytest

from career_copilot.resume import storage as module
from career_copilot.resume.storage import (
    BinaryTooLarge,
    EmptyBinary,
    LocalResumeStorage,
    StorageUnavailable,
)


async def chunks(*values):
    for value in values:
        yield value


@pytest.fixture
def storage(tmp_path):
    return LocalResumeStorage(tmp_path / "private")


def test_new_directories_have_private_mode(tmp_path):
    storage = LocalResumeStorage(tmp_path / "private")
    for directory in (storage.root, storage.temporary):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_existing_private_directories_are_accepted(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    (root / ".tmp").mkdir(mode=0o700)
    storage = LocalResumeStorage(root)
    assert storage.root == root
    assert list(storage.temporary.iterdir()) == []


@pytest.mark.parametrize("mode", [0o777, 0o770, 0o755, 0o750, 0o1700])
@pytest.mark.parametrize("directory", ["root", "temporary"])
def test_existing_insecure_directory_rejected_without_chmod(tmp_path, mode, directory):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    temporary = root / ".tmp"
    temporary.mkdir(mode=0o700)
    insecure = root if directory == "root" else temporary
    insecure.chmod(mode)
    with pytest.raises(StorageUnavailable) as error:
        LocalResumeStorage(root)
    assert str(error.value) == ""
    assert stat.S_IMODE(insecure.stat().st_mode) == mode


async def test_save_actual_bytes_private_modes_and_idempotent_delete(storage):
    key = uuid4().hex
    saved = await storage.save(key, chunks(b"abc", b"def"), 6)
    assert saved.size_bytes == 6
    assert (storage.root / key).read_bytes() == b"abcdef"
    assert list(storage.temporary.iterdir()) == []
    assert storage.root.stat().st_mode & 0o777 == 0o700
    assert (storage.root / key).stat().st_mode & 0o777 == 0o600
    await storage.delete(key)
    await storage.delete(key)
    assert not (storage.root / key).exists()


@pytest.mark.parametrize(
    "values,limit,error", [([b"a", b"bc"], 2, BinaryTooLarge), ([], 2, EmptyBinary)]
)
async def test_invalid_size_cleans_partial_and_stops_consumption(
    storage, values, limit, error
):
    key = uuid4().hex
    with pytest.raises(error):
        await storage.save(key, chunks(*values), limit)
    assert list(storage.root.iterdir()) == [storage.temporary]
    assert list(storage.temporary.iterdir()) == []


async def test_limit_stops_before_reading_remaining_stream(storage):
    async def source():
        yield b"ab"
        yield b"c"
        pytest.fail("Oversized streams must not be drained")

    with pytest.raises(BinaryTooLarge):
        await storage.save(uuid4().hex, source(), 2)
    assert list(storage.temporary.iterdir()) == []


@pytest.mark.parametrize(
    "key", ["../../outside", "../" + "a" * 32, "A" * 32, "abc", "/tmp/x"]
)
async def test_invalid_key_rejected_for_save_and_delete(storage, key):
    with pytest.raises(StorageUnavailable):
        await storage.save(key, chunks(b"a"), 1)
    with pytest.raises(StorageUnavailable):
        await storage.delete(key)
    with pytest.raises(StorageUnavailable):
        await storage.read(key, 10)
    assert list(storage.temporary.iterdir()) == []


async def test_collision_never_overwrites_or_deletes_existing_binary(storage):
    key = uuid4().hex
    await storage.save(key, chunks(b"original"), 20)
    with pytest.raises(StorageUnavailable):
        await storage.save(key, chunks(b"replacement"), 20)
    assert (storage.root / key).read_bytes() == b"original"
    assert list(storage.temporary.iterdir()) == []


async def test_symlink_target_and_temporary_directory_are_rejected(storage, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"private outside")
    key = uuid4().hex
    (storage.root / key).symlink_to(outside)
    with pytest.raises(StorageUnavailable):
        await storage.delete(key)
    with pytest.raises(StorageUnavailable):
        await storage.save(key, chunks(b"replacement"), 20)
    assert outside.read_bytes() == b"private outside"
    storage.temporary.rmdir()
    storage.temporary.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(StorageUnavailable):
        await storage.save(uuid4().hex, chunks(b"a"), 1)


@pytest.mark.parametrize("operation", ["write", "fsync", "link"])
async def test_os_failures_clean_owned_files(storage, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise OSError("private OS text /private/server/path")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(StorageUnavailable) as error:
        await storage.save(uuid4().hex, chunks(b"content"), 20)
    assert str(error.value) == ""
    assert list(storage.temporary.iterdir()) == []
    assert list(storage.root.iterdir()) == [storage.temporary]


async def test_directory_sync_failure_after_publish_removes_final(storage, monkeypatch):
    def fail():
        raise OSError("private fsync failure")

    monkeypatch.setattr(storage, "_sync_directory", fail)
    with pytest.raises(StorageUnavailable):
        await storage.save(uuid4().hex, chunks(b"content"), 20)
    assert list(storage.root.iterdir()) == [storage.temporary]
    assert list(storage.temporary.iterdir()) == []


async def test_cancelled_stream_cleans_temporary(storage):
    ready = asyncio.Event()

    async def source():
        yield b"partial"
        ready.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(storage.save(uuid4().hex, source(), 20))
    await asyncio.wait_for(ready.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(storage.temporary.iterdir()) == []
    assert list(storage.root.iterdir()) == [storage.temporary]


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_finalize_waits_for_thread_then_cleans_binary(
    storage, monkeypatch, cancellations
):
    started, release = threading.Event(), threading.Event()
    original = os.link

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "link", delayed)
    task = asyncio.create_task(storage.save(uuid4().hex, chunks(b"content"), 20))
    try:
        await asyncio.wait_for(module.run_io(started.wait, 5), 6)
        assert started.is_set()
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert list(storage.temporary.iterdir()) == []
    assert list(storage.root.iterdir()) == [storage.temporary]


async def test_cleanup_failure_logs_only_opaque_key(storage, monkeypatch, caplog):
    key = uuid4().hex
    original = module.Path.unlink

    def fail(path, *args, **kwargs):
        if path.parent == storage.temporary:
            raise PermissionError("private OS path and error")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module.Path, "unlink", fail)
    with pytest.raises(StorageUnavailable):
        await storage.save(key, chunks(b"too large"), 1)
    assert key in caplog.text
    assert "operation=save" in caplog.text
    assert str(storage.root) not in caplog.text
    assert "private OS path" not in caplog.text


async def test_read_actual_binary_and_exact_bound(storage):
    key = uuid4().hex
    await storage.save(key, chunks(b"binary bytes"), 20)
    assert await storage.read(key, 12) == b"binary bytes"
    with pytest.raises(StorageUnavailable) as error:
        await storage.read(key, 11)
    assert str(error.value) == ""
    assert (storage.root / key).read_bytes() == b"binary bytes"


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "fifo"])
async def test_read_rejects_unsafe_objects_without_blocking(
    storage, tmp_path, kind, monkeypatch
):
    key = uuid4().hex
    target = storage.root / key
    if kind == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(b"private outside")
        target.symlink_to(outside)
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    original_open = os.open
    flags_seen = []

    def checked_open(path, flags, *args, **kwargs):
        flags_seen.append(flags)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", checked_open)
    with pytest.raises(StorageUnavailable) as error:
        await asyncio.wait_for(storage.read(key, 10), 2)
    assert str(error.value) == ""
    assert flags_seen and all(
        flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW and flags & os.O_CLOEXEC
        for flags in flags_seen
    )


@pytest.mark.parametrize("directory", ["root", "temporary"])
async def test_read_rejects_insecure_directories(storage, directory):
    key = uuid4().hex
    await storage.save(key, chunks(b"content"), 20)
    getattr(storage, directory).chmod(0o755)
    with pytest.raises(StorageUnavailable):
        await storage.read(key, 20)


@pytest.mark.parametrize("operation", ["open", "fstat", "read"])
async def test_read_os_errors_close_descriptor_and_hide_details(
    storage, monkeypatch, operation
):
    key = uuid4().hex
    await storage.save(key, chunks(b"content"), 20)
    opened, closed = [], []
    original_open, original_close = os.open, os.close

    def tracking_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def tracking_close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)

    def fail(*args, **kwargs):
        raise PermissionError("private OS detail /private/path")

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(StorageUnavailable) as error:
        await storage.read(key, 20)
    assert str(error.value) == ""
    assert opened == closed


async def test_read_counts_actual_bytes_even_if_stat_size_is_stale(
    storage, monkeypatch
):
    key = uuid4().hex
    await storage.save(key, chunks(b"a" * 100), 100)
    original_fstat, original_read = os.fstat, os.read
    requested = []

    def small_stat(descriptor):
        values = list(original_fstat(descriptor))
        values[6] = 1
        return os.stat_result(values)

    def checked_read(descriptor, amount):
        requested.append(amount)
        return original_read(descriptor, amount)

    monkeypatch.setattr(os, "fstat", small_stat)
    monkeypatch.setattr(os, "read", checked_read)
    with pytest.raises(StorageUnavailable):
        await storage.read(key, 5)
    assert requested == [6]


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_read_finishes_and_closes_descriptor(
    storage, monkeypatch, cancellations
):
    key = uuid4().hex
    await storage.save(key, chunks(b"content"), 20)
    started, release = threading.Event(), threading.Event()
    original_read, original_close = os.read, os.close
    closed = []

    def blocked_read(descriptor, amount):
        started.set()
        assert release.wait(5)
        return original_read(descriptor, amount)

    def checked_close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(os, "read", blocked_read)
    monkeypatch.setattr(os, "close", checked_close)
    before = asyncio.all_tasks()
    task = asyncio.create_task(storage.read(key, 20))
    try:
        assert await asyncio.wait_for(module.run_io(started.wait, 5), 6)
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert closed == []
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert len(closed) == 1
    assert not (asyncio.all_tasks() - before)
