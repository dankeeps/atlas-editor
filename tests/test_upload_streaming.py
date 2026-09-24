"""Large upload contracts without allocating large files or invoking paid services."""
import errno
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import cloud

MIB = 1024**2
LIMIT = 20 * 1024**3
RESERVE = 64 * MIB


def handler(size, reader=None):
    return SimpleNamespace(headers={"Content-Length": str(size)}, rfile=reader or io.BytesIO())


class VirtualStream:
    """One reusable buffer represents an arbitrarily large request body."""
    def __init__(self, size, before_read=None):
        self.remaining = size
        self.buffer = b"x" * MIB
        self.calls = 0
        self.max_request = 0
        self.before_read = before_read

    def read(self, size):
        if size <= 0 or size > MIB:
            raise AssertionError(f"Streaming reads must be bounded by 1 MiB, requested {size}")
        if self.before_read:
            self.before_read()
        self.calls += 1
        self.max_request = max(self.max_request, size)
        count = min(size, self.remaining)
        self.remaining -= count
        return self.buffer if count == MIB else self.buffer[:count]


class CountingWriter:
    """Track the stream; write only a marker to exercise the real atomic rename."""
    def __init__(self, file, fail_errno=None):
        self.file = file
        self.fail_errno = fail_errno
        self.bytes = 0
        self.calls = 0
        self.max_write = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.file.close()

    def write(self, data):
        self.calls += 1
        self.max_write = max(self.max_write, len(data))
        if self.fail_errno and self.calls == 2:
            raise OSError(self.fail_errno, "Storage consumed by another upload")
        if not self.bytes:
            self.file.write(b"new upload marker")
        self.bytes += len(data)
        return len(data)


class UploadStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atlas-streaming-")
        self.root = Path(self.temp.name)
        self.target = self.root / "video.mp4"
        self.target.write_bytes(b"previous complete file")
        self.maximum = patch.object(cloud, "MAX_UPLOAD", LIMIT)
        self.maximum.start()
        self.addCleanup(self.maximum.stop)
        self.addCleanup(self.temp.cleanup)

    def disk(self, free=LIMIT + RESERVE):
        return patch.object(cloud.shutil, "disk_usage", return_value=SimpleNamespace(total=free, used=0, free=free))

    def unchanged(self):
        self.assertEqual(self.target.read_bytes(), b"previous complete file")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_customer_sizes_and_limit_are_checked_as_integers(self):
        for size in (8925617592, 11570000000, LIMIT):
            with self.subTest(size=size):
                self.assertEqual(cloud.body_size(handler(size)), size)
        with self.assertRaises(cloud.UploadError) as failure:
            cloud.body_size(handler(LIMIT + 1))
        self.assertEqual(failure.exception.status, 413)
        # Changing configuration in-process must not leave a captured old default.
        with patch.object(cloud, "MAX_UPLOAD", 50):
            self.assertEqual(cloud.body_size(handler(50)), 50)
            with self.assertRaises(cloud.UploadError) as smaller:
                cloud.body_size(handler(51))
            self.assertEqual(smaller.exception.status, 413)
        self.assertEqual(cloud.body_size(handler(99), limit=100), 99)

    def test_fresh_configuration_defaults_to_20gib_and_accepts_explicit_override(self):
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "app")}
        env.pop("ESTUDIO_MAX_UPLOAD_BYTES", None)
        for override, expected in ((None, LIMIT), ("123456789", 123456789)):
            with self.subTest(override=override):
                if override is not None:
                    env["ESTUDIO_MAX_UPLOAD_BYTES"] = override
                result = subprocess.run([sys.executable, "-c", "import cloud; print(cloud.MAX_UPLOAD)"],
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(int(result.stdout.strip()), expected)

    def test_virtual_11gb_stream_uses_bounded_reads_and_atomic_replacement(self):
        size = 11570000000
        writes = []
        real_open = open
        checks = [0]
        def before_read():
            checks[0] += 1
            if checks[0] in (1, 10, 1000):
                self.assertEqual(self.target.read_bytes(), b"previous complete file")
        stream = VirtualStream(size, before_read)
        def counting_open(path, mode):
            self.assertEqual(mode, "xb")
            self.assertTrue(str(path).endswith(".part"))
            writer = CountingWriter(real_open(path, mode))
            writes.append(writer)
            return writer
        with self.disk(free=size + RESERVE), patch.object(cloud, "open", counting_open, create=True):
            cloud.receive(handler(size, stream), str(self.target))
        self.assertEqual(stream.remaining, 0)
        self.assertEqual(stream.calls, (size + MIB - 1) // MIB)
        self.assertEqual(stream.max_request, MIB)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].bytes, size)
        self.assertLessEqual(writes[0].max_write, MIB)
        self.assertEqual(self.target.read_bytes(), b"new upload marker")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_insufficient_space_is_rejected_before_reading_or_opening_output(self):
        size = 11570000000
        reader = Mock()
        reader.read.side_effect = AssertionError("Do not read a 10 GB body when disk is full")
        for free in (0, size + RESERVE - 1):
            with self.subTest(free=free), self.disk(free), patch.object(cloud, "open", create=True) as output:
                with self.assertRaises(cloud.UploadError) as failure:
                    cloud.receive(handler(size, reader), str(self.target))
                self.assertEqual(failure.exception.status, 507)
                output.assert_not_called()
                reader.read.assert_not_called()
            self.unchanged()

    def test_interrupted_stream_removes_partial_but_keeps_complete_file(self):
        with self.disk(), self.assertRaises(ValueError):
            cloud.receive(handler(500, io.BytesIO(b"interrupted")), str(self.target))
        self.unchanged()

    def test_space_consumed_after_preflight_returns_507_and_cleans_partial(self):
        for number in (errno.ENOSPC, errno.EDQUOT):
            with self.subTest(errno=number):
                writes = []
                real_open = open
                def full_open(path, mode):
                    writer = CountingWriter(real_open(path, mode), fail_errno=number)
                    writes.append(writer)
                    return writer
                with self.disk(), patch.object(cloud, "open", full_open, create=True):
                    with self.assertRaises(cloud.UploadError) as failure:
                        cloud.receive(handler(3 * MIB, VirtualStream(3 * MIB)), str(self.target))
                self.assertEqual(failure.exception.status, 507)
                self.assertEqual(writes[0].calls, 2)
                self.unchanged()

    def test_no_space_opening_or_committing_output_returns_507(self):
        with self.disk(), patch.object(cloud, "open", side_effect=OSError(errno.ENOSPC, "disk full"), create=True):
            with self.assertRaises(cloud.UploadError) as failure:
                cloud.receive(handler(3, io.BytesIO(b"new")), str(self.target))
        self.assertEqual(failure.exception.status, 507)
        self.unchanged()
        with self.disk(), patch.object(cloud.os, "replace", side_effect=OSError(errno.EDQUOT, "quota full")):
            with self.assertRaises(cloud.UploadError) as failure:
                cloud.receive(handler(3, io.BytesIO(b"new")), str(self.target))
        self.assertEqual(failure.exception.status, 507)
        self.unchanged()

    def test_unrelated_io_error_is_not_misreported_as_disk_full(self):
        with self.disk(), patch.object(cloud, "open", side_effect=OSError(errno.EACCES, "forbidden"), create=True):
            with self.assertRaises(OSError) as failure:
                cloud.receive(handler(3, io.BytesIO(b"new")), str(self.target))
        self.assertEqual(failure.exception.errno, errno.EACCES)
        self.unchanged()

    def test_bad_or_empty_lengths_never_replace_target(self):
        for raw in ("-1", "1.5", "invalid", "1000000000000"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                cloud.body_size(handler(raw))
        chunked = handler(5); chunked.headers["Transfer-Encoding"] = "chunked"
        with self.assertRaises(ValueError):
            cloud.body_size(chunked)
        with self.disk(), self.assertRaises(ValueError):
            cloud.receive(handler(0), str(self.target))
        self.unchanged()


if __name__ == "__main__":
    unittest.main(verbosity=2)
