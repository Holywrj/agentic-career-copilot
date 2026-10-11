"""Small binary boundary and a private, durable local filesystem implementation."""

import asyncio
import logging
import os
import re
import stat
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

import anyio

logger = logging.getLogger(__name__)
T = TypeVar("T")


class StorageUnavailable(Exception):
    """An expected storage failure, without private OS details."""


class BinaryTooLarge(Exception):
    pass


class EmptyBinary(Exception):
    pass


@dataclass(frozen=True)
class StoredBinary:
    size_bytes: int


class ResumeStorage(Protocol):
    async def save(
        self, key: str, chunks: AsyncIterator[bytes], max_bytes: int
    ) -> StoredBinary: ...

    async def delete(self, key: str) -> None: ...


async def complete(operation: Awaitable[T]) -> T:
    """Finish owned work before propagating cancellation; never abandon a writer."""
    with anyio.CancelScope(shield=True):
        task = asyncio.ensure_future(operation)
        cancellation = None
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError as error:
                cancellation = error
                if task.done():
                    break
            except BaseException:
                if cancellation is None:
                    raise
                break
        if cancellation is not None:
            # Retrieve any child error while preserving the caller cancellation.
            if not task.cancelled():
                task.exception()
            raise cancellation
        return result


async def run_io(function: Callable[..., T], *args) -> T:
    return await complete(anyio.to_thread.run_sync(function, *args))


class LocalResumeStorage:
    def __init__(self, root: Path) -> None:
        # Construction is invoked in a thread at startup, never at import time.
        try:
            self.root = root.resolve()
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.temporary = self.root / ".tmp"
            self.temporary.mkdir(mode=0o700, exist_ok=True)
            self._check_directories()
            # Verify writability without leaving a persistent startup artifact.
            with tempfile.TemporaryFile(dir=self.temporary):
                pass
        except OSError:
            raise StorageUnavailable() from None

    def _check_directories(self) -> None:
        for directory in (self.root, self.temporary):
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise StorageUnavailable()

    def _path(self, key: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}", key) is None:
            raise StorageUnavailable()
        return self.root / key

    def _sync_directory(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    async def save(
        self, key: str, chunks: AsyncIterator[bytes], max_bytes: int
    ) -> StoredBinary:
        target = self._path(key)
        temporary: Path | None = None
        descriptor: int | None = None
        published = False
        succeeded = False

        def open_temporary() -> None:
            nonlocal temporary, descriptor
            self._check_directories()
            descriptor, name = tempfile.mkstemp(dir=self.temporary)
            temporary = Path(name)

        def write(chunk: bytes) -> None:
            assert descriptor is not None
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("write did not advance")
                remaining = remaining[written:]

        def finalize() -> None:
            nonlocal descriptor, published
            assert descriptor is not None and temporary is not None
            self._check_directories()
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            # Atomic publication on the same filesystem, with no overwrite even
            # if a generator/import bug reuses an existing storage key.
            os.link(temporary, target, follow_symlinks=False)
            published = True
            temporary.unlink()
            self._sync_directory()

        def cleanup() -> None:
            nonlocal descriptor
            try:
                if descriptor is not None:
                    os.close(descriptor)
                    descriptor = None
            finally:
                try:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                finally:
                    if published and not succeeded:
                        target.unlink(missing_ok=True)
                        self._sync_directory()

        try:
            await run_io(open_temporary)
            size = 0
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise BinaryTooLarge()
                await run_io(write, chunk)
            if size == 0:
                raise EmptyBinary()
            await run_io(finalize)
            succeeded = True
            return StoredBinary(size_bytes=size)
        except OSError:
            raise StorageUnavailable() from None
        finally:
            if not succeeded:
                try:
                    await run_io(cleanup)
                except (OSError, StorageUnavailable):
                    logger.error(
                        "resume_storage_cleanup_failed operation=save key=%s", key
                    )
                    raise StorageUnavailable() from None

    async def delete(self, key: str) -> None:
        target = self._path(key)

        def remove() -> None:
            self._check_directories()
            try:
                mode = target.lstat().st_mode
            except FileNotFoundError:
                # Sync also completes a previous unlink whose directory fsync failed.
                self._sync_directory()
                return
            if not stat.S_ISREG(mode):
                raise StorageUnavailable()
            target.unlink()
            self._sync_directory()

        try:
            await run_io(remove)
        except OSError:
            raise StorageUnavailable() from None
