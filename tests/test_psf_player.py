"""
tests/test_psf_player.py - Comprehensive unit test suite for native PSF/PSF2 playback.

Tests:
1. Library loading & error reporting
2. Invalid paths
3. Unsupported extensions / non-PSF formats
4. Seeking behavior & unsupported seek exceptions
5. Clean shutdown cycles
6. Metadata parsing
7. PCM rendering integrity
"""

import os
import sys
import tempfile
import unittest

# Ensure workspace site-packages is in sys.path
_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_venv_site = os.path.join(_workspace, ".venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")
if os.path.exists(_venv_site) and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from psf_ctypes import (
    AOSDKContext,
    AOSDKError,
    AOSDKLoadError,
    AOSDKOpenError,
    AOSDKSeekError,
    _AOSDKBinding,
    find_aosdk_lib,
)


class TestAOSDKLoading(unittest.TestCase):
    """Tests library discovery and loading errors."""

    def test_find_aosdk_lib_success(self):
        lib_path = find_aosdk_lib()
        self.assertTrue(os.path.isfile(lib_path), f"Library not found at: {lib_path}")

    def test_library_load_error_on_invalid_path(self):
        with self.assertRaises(AOSDKLoadError):
            _AOSDKBinding("/nonexistent/path/to/libaosdk_bogus.so")


class TestAOSDKFileHandling(unittest.TestCase):
    """Tests opening invalid paths, unsupported extensions, and invalid formats."""

    def setUp(self):
        self.ctx = AOSDKContext()

    def tearDown(self):
        self.ctx.close()

    def test_invalid_path(self):
        with self.assertRaises(AOSDKOpenError) as cm:
            self.ctx.open("/nonexistent/file/does_not_exist.psf")
        self.assertIn("File not found", str(cm.exception))

    def test_unsupported_extension_and_invalid_signature(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"This is just a plain text file, not a PSF or PSF2 audio file.")
            temp_path = f.name

        try:
            with self.assertRaises(AOSDKOpenError) as cm:
                self.ctx.open(temp_path)
            self.assertIn("Invalid signature", str(cm.exception))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_truncated_file(self):
        with tempfile.NamedTemporaryFile(suffix=".psf", delete=False) as f:
            f.write(b"PSF\x01")  # truncated to 4 bytes
            temp_path = f.name

        try:
            with self.assertRaises(AOSDKOpenError) as cm:
                self.ctx.open(temp_path)
            self.assertIn("File too small", str(cm.exception))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class TestAOSDKSeeking(unittest.TestCase):
    """Tests seeking behavior: resetting to 0 vs unsupported non-zero seek."""

    def setUp(self):
        self.ctx = AOSDKContext()
        self.psf_path = os.path.join(_workspace, "aosdk", "samples", "pe.psf")

    def tearDown(self):
        self.ctx.close()

    def test_unsupported_seek_raises_exception(self):
        self.ctx.open(self.psf_path)
        with self.assertRaises(AOSDKSeekError) as cm:
            self.ctx.seek(15.0)
        self.assertIn("unsupported", str(cm.exception).lower())

    def test_seek_to_start_resets(self):
        self.ctx.open(self.psf_path)
        # Render 1 second
        self.ctx.render(44100)
        pos = self.ctx.get_position()
        self.assertGreaterEqual(pos, 0.9)

        # Seek to 0.0 (reset)
        self.ctx.seek(0.0)
        pos_after = self.ctx.get_position()
        self.assertAlmostEqual(pos_after, 0.0, places=2)


class TestAOSDKPlaybackAndShutdown(unittest.TestCase):
    """Tests repeated lifecycle cycles, metadata parsing, and rendering integrity."""

    def setUp(self):
        self.psf_path = os.path.join(_workspace, "aosdk", "samples", "pe.psf")
        self.psf2_path = os.path.join(_workspace, "aosdk", "samples", "01.psf2")

    def test_clean_shutdown_cycles(self):
        """Repeatedly create, open, render, stop, and close contexts to verify clean lifecycle."""
        for i in range(20):
            with AOSDKContext() as ctx:
                track = self.psf_path if (i % 2 == 0) else self.psf2_path
                ctx.open(track)
                self.assertTrue(ctx.is_open())
                count, pcm = ctx.render(1024)
                self.assertEqual(count, 1024)
                self.assertEqual(len(pcm), 1024 * 4)
                ctx.stop()
                self.assertFalse(ctx.is_open())

    def test_metadata_extraction_psf(self):
        with AOSDKContext() as ctx:
            ctx.open(self.psf_path)
            self.assertEqual(ctx.format_name, "PlayStation (PSF)")
            dur = ctx.get_duration()
            self.assertIsNotNone(dur)
            self.assertAlmostEqual(dur, 117.0, places=1)
            self.assertAlmostEqual(ctx.get_fade(), 10.0, places=1)

            tags = ctx.get_tags()
            self.assertEqual(tags.get("title"), "110 Main Theme (Piano Solo)")
            self.assertEqual(tags.get("game"), "Parasite Eve")
            self.assertEqual(tags.get("artist"), "Yoko Shimomura")

    def test_metadata_extraction_psf2(self):
        with AOSDKContext() as ctx:
            ctx.open(self.psf2_path)
            self.assertEqual(ctx.format_name, "PlayStation 2 (PSF2)")
            dur = ctx.get_duration()
            self.assertIsNotNone(dur)
            self.assertAlmostEqual(dur, 180.0, places=1)
            self.assertAlmostEqual(ctx.get_fade(), 5.0, places=1)

            tags = ctx.get_tags()
            self.assertEqual(tags.get("title"), "01")
            self.assertEqual(tags.get("game"), "BLACK/MATRIX 2")


class TestPSFAudioWorker(unittest.TestCase):
    """Tests the dedicated audio worker thread and queue communication."""

    def test_worker_lifecycle_and_queue(self):
        import queue
        import time
        from players import PSFAudioWorker

        psf_path = os.path.join(_workspace, "aosdk", "samples", "pe.psf")
        ui_q = queue.Queue()

        worker = PSFAudioWorker(
            audio_file=psf_path,
            ui_queue=ui_q,
            volume=0.8,
            loop_forever=False,
            loop_count=1
        )
        worker.start()

        # Collect messages for 1 second
        messages = []
        start = time.time()
        while time.time() - start < 1.0:
            try:
                msg = ui_q.get(timeout=0.1)
                messages.append(msg)
            except queue.Empty:
                pass

        # Test pause and resume
        worker.pause()
        self.assertTrue(worker.is_paused())
        worker.resume()
        self.assertFalse(worker.is_paused())

        # Test volume update
        worker.set_volume(0.5)
        self.assertEqual(worker.volume, 0.5)

        # Stop worker
        worker.stop()
        self.assertFalse(worker._thread.is_alive())

        # Verify we received 'opened', 'status' (Playing), and 'position' messages
        msg_types = [m[0] for m in messages]
        self.assertIn("opened", msg_types)
        self.assertIn("status", msg_types)
        self.assertIn("position", msg_types)


if __name__ == "__main__":
    unittest.main()
