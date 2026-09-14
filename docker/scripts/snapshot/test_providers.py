"""
Unit tests for GKE Snapshot Providers, Cache Clearing, and vLLM Lifespan Wrapper.
"""

from __future__ import annotations

import asyncio
import errno
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from docker.scripts.snapshot import (
    GKESnapshotProvider,
    SnapshotError,
    get_snapshot_provider,
    patch_vllm_lifespan,
)

CHECKPOINT_PATH = "/proc/gvisor/checkpoint"
MOCK_FD = 42
NONEXISTENT_PATH = "/nonexistent/path/to/cache"


class TestClearCache(unittest.TestCase):
    def test_clear_cache_with_explicit_path_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = os.path.join(tmpdir, "models--org--repo")
            os.makedirs(model_dir)
            file_path = os.path.join(model_dir, "weights.bin")
            with open(file_path, "w") as f:
                f.write("dummy content")

            provider = GKESnapshotProvider(cache_dir=tmpdir)
            self.assertTrue(os.path.exists(model_dir))
            provider.clear_cache()
            self.assertFalse(os.path.exists(model_dir))

    def test_clear_cache_with_explicit_path_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("dummy file")
            file_path = f.name

        try:
            provider = GKESnapshotProvider(cache_dir=file_path)
            self.assertTrue(os.path.exists(file_path))
            provider.clear_cache()
            self.assertFalse(os.path.exists(file_path))
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    def test_clear_cache_with_env_var(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = os.path.join(tmpdir, "models--org--repo")
            os.makedirs(model_dir)
            with patch.dict(os.environ, {"MODEL_CACHE_DIR": tmpdir}):
                provider = GKESnapshotProvider()
                self.assertTrue(os.path.exists(model_dir))
                provider.clear_cache()
                self.assertFalse(os.path.exists(model_dir))

    def test_clear_cache_non_existent(self):
        provider = GKESnapshotProvider(cache_dir=NONEXISTENT_PATH)
        # Should not raise exception
        provider.clear_cache()

    def test_clear_cache_removes_all_entries_in_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir1 = os.path.join(tmpdir, "models--org--repo1")
            model_dir2 = os.path.join(tmpdir, "custom_model_dir")
            other_file = os.path.join(tmpdir, "weights.bin")

            os.makedirs(model_dir1)
            os.makedirs(model_dir2)
            with open(other_file, "w") as f:
                f.write("weights")

            provider = GKESnapshotProvider(cache_dir=tmpdir)
            provider.clear_cache()

            self.assertFalse(os.path.exists(model_dir1))
            self.assertFalse(os.path.exists(model_dir2))
            self.assertFalse(os.path.exists(other_file))
            self.assertTrue(os.path.exists(tmpdir))

    def test_clear_cache_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = GKESnapshotProvider(cache_dir=tmpdir)
            provider.clear_cache()
            self.assertTrue(os.path.exists(tmpdir))

    def test_clear_cache_failure_raises_snapshot_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            model_dir = os.path.join(tmpdir, "models--org--repo")
            os.makedirs(model_dir)
            provider = GKESnapshotProvider(cache_dir=tmpdir)
            with patch("shutil.rmtree", side_effect=PermissionError("Permission denied")):
                with self.assertRaises(SnapshotError) as ctx:
                    provider.clear_cache()
            self.assertIn("Could not delete locally stored weights", str(ctx.exception))


class TestGKESnapshotProvider(unittest.TestCase):
    @patch("os.access", return_value=False)
    @patch.object(GKESnapshotProvider, "clear_cache")
    @patch("docker.scripts.snapshot.providers.logger")
    def test_open_checkpoint_file_not_writable(self, mock_logger, mock_clear, mock_access):
        provider = GKESnapshotProvider(proc_path=CHECKPOINT_PATH)
        provider.trigger()

        mock_access.assert_called_once_with(CHECKPOINT_PATH, os.W_OK)
        mock_clear.assert_not_called()
        mock_logger.warning.assert_called()
        warnings = [call[0][0] for call in mock_logger.warning.call_args_list]
        self.assertTrue(any("Pod snapshot trigger not available" in w for w in warnings))

    @patch("os.access")
    def test_is_available(self, mock_access):
        mock_access.return_value = True
        provider = GKESnapshotProvider(proc_path=CHECKPOINT_PATH)
        self.assertTrue(provider.is_available())
        mock_access.assert_called_with(CHECKPOINT_PATH, os.W_OK)

        mock_access.return_value = False
        self.assertFalse(provider.is_available())

    @patch("os.access", return_value=True)
    @patch.object(GKESnapshotProvider, "clear_cache")
    @patch("os.open")
    @patch("os.write")
    @patch("os.read")
    @patch("os.close")
    def test_gke_snapshot_provider_success(
        self, mock_close, mock_read, mock_write, mock_open, mock_clear, mock_access
    ):
        mock_open.return_value = MOCK_FD
        mock_read.return_value = b""

        provider = GKESnapshotProvider(proc_path=CHECKPOINT_PATH)
        provider.trigger()

        mock_access.assert_called_once_with(CHECKPOINT_PATH, os.W_OK)
        mock_clear.assert_called_once()
        mock_open.assert_called_once_with(CHECKPOINT_PATH, os.O_RDWR)
        mock_write.assert_called_once_with(MOCK_FD, b"1")
        mock_read.assert_called_once_with(MOCK_FD, 1)
        mock_close.assert_called_once_with(MOCK_FD)

    @patch("os.access", return_value=True)
    @patch.object(GKESnapshotProvider, "clear_cache")
    @patch("os.open")
    @patch("os.write")
    @patch("os.read")
    @patch("os.close")
    def test_gke_snapshot_provider_einval_failure(
        self, mock_close, mock_read, mock_write, mock_open, mock_clear, mock_access
    ):
        mock_open.return_value = MOCK_FD
        err = OSError()
        err.errno = errno.EINVAL
        mock_read.side_effect = err

        provider = GKESnapshotProvider()
        with self.assertRaises(SnapshotError) as ctx:
            provider.trigger()
        self.assertIn("Snapshot was never initiated", str(ctx.exception))


class TestGKEVllmWrapper(unittest.TestCase):
    def test_patch_vllm_lifespan(self):
        mock_app = MagicMock()
        mock_router = MagicMock()

        @asynccontextmanager
        async def mock_original_lifespan(app):
            yield {"status": "ok"}

        mock_router.lifespan_context = mock_original_lifespan
        mock_app.router = mock_router

        mock_engine = AsyncMock()
        mock_engine.sleep = AsyncMock()
        mock_engine.wake_up = AsyncMock()
        mock_state = MagicMock(spec=["engine_client"])
        mock_state.engine_client = mock_engine
        mock_app.state = mock_state

        mock_provider = MagicMock(spec=GKESnapshotProvider)

        patched_app = patch_vllm_lifespan(mock_app, snapshot_provider=mock_provider)
        self.assertIsNotNone(patched_app)
        self.assertNotEqual(mock_router.lifespan_context, mock_original_lifespan)

        async def run_test():
            async with mock_router.lifespan_context(mock_app) as state:
                self.assertEqual(state, {"status": "ok"})

        asyncio.run(run_test())

        mock_engine.sleep.assert_called_once_with(level=1)
        mock_provider.trigger.assert_called_once()
        mock_engine.wake_up.assert_called_once()

    def test_patch_vllm_lifespan_disabled_provider(self):
        mock_app = MagicMock()
        mock_router = MagicMock()

        @asynccontextmanager
        async def mock_original_lifespan(app):
            yield {"status": "ok"}

        mock_router.lifespan_context = mock_original_lifespan
        mock_app.router = mock_router
        mock_engine = AsyncMock()
        mock_engine.sleep = AsyncMock()
        mock_engine.wake_up = AsyncMock()
        mock_app.state = MagicMock(engine_client=mock_engine)
        with patch.dict(os.environ, {"SNAPSHOT_PROVIDER": "none"}):
            patched_app = patch_vllm_lifespan(mock_app)
            self.assertEqual(patched_app.router.lifespan_context, mock_original_lifespan)
            async def run_test():
                async with mock_router.lifespan_context(mock_app) as state:
                    self.assertEqual(state, {"status": "ok"})
            asyncio.run(run_test())

        mock_engine.sleep.assert_not_called()
        mock_engine.wake_up.assert_not_called()

    def test_patch_vllm_lifespan_empty_string_provider(self):
        mock_app = MagicMock()
        mock_router = MagicMock()

        @asynccontextmanager
        async def mock_original_lifespan(app):
            yield {"status": "ok"}

        mock_router.lifespan_context = mock_original_lifespan
        mock_app.router = mock_router
        mock_engine = AsyncMock()
        mock_engine.sleep = AsyncMock()
        mock_engine.wake_up = AsyncMock()
        mock_app.state = MagicMock(engine_client=mock_engine)
        with patch.dict(os.environ, {"SNAPSHOT_PROVIDER": ""}):
            patched_app = patch_vllm_lifespan(mock_app)
            self.assertEqual(patched_app.router.lifespan_context, mock_original_lifespan)
            async def run_test():
                async with mock_router.lifespan_context(mock_app) as state:
                    self.assertEqual(state, {"status": "ok"})
            asyncio.run(run_test())

        mock_engine.sleep.assert_not_called()
        mock_engine.wake_up.assert_not_called()

    def test_patch_vllm_lifespan_provider_not_available_skips_sleep_and_wake(self):
        mock_app = MagicMock()
        mock_router = MagicMock()

        @asynccontextmanager
        async def mock_original_lifespan(app):
            yield {"status": "ok"}

        mock_router.lifespan_context = mock_original_lifespan
        mock_app.router = mock_router
        mock_engine = AsyncMock()
        mock_engine.sleep = AsyncMock()
        mock_engine.wake_up = AsyncMock()
        mock_app.state = MagicMock(engine_client=mock_engine)

        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = False
        mock_provider.proc_path = "/proc/gvisor/checkpoint"

        with patch("docker.scripts.snapshot.vllm.wrapper.logger") as mock_logger:
            patch_vllm_lifespan(mock_app, snapshot_provider=mock_provider)
            async def run_test():
                async with mock_router.lifespan_context(mock_app) as state:
                    self.assertEqual(state, {"status": "ok"})
            asyncio.run(run_test())

            mock_engine.sleep.assert_not_called()
            mock_provider.trigger.assert_not_called()
            mock_engine.wake_up.assert_not_called()
            mock_logger.warning.assert_called()
            warnings = [call[0][0] for call in mock_logger.warning.call_args_list]
            self.assertTrue(any("Pod snapshot trigger not available" in w for w in warnings))

    def test_patch_vllm_lifespan_unsupported_engine_logs_error(self):
        mock_app = MagicMock()
        mock_router = MagicMock()

        @asynccontextmanager
        async def mock_original_lifespan(app):
            yield {"status": "ok"}

        mock_router.lifespan_context = mock_original_lifespan
        mock_app.router = mock_router
        mock_app.state = MagicMock(engine_client=object())
        mock_provider = MagicMock(spec=GKESnapshotProvider)

        with patch("docker.scripts.snapshot.vllm.wrapper.logger") as mock_logger:
            patched_app = patch_vllm_lifespan(mock_app, snapshot_provider=mock_provider)
            async def run_test():
                async with mock_router.lifespan_context(mock_app) as state:
                    self.assertEqual(state, {"status": "ok"})
            asyncio.run(run_test())

            error_messages = [call[0][0] for call in mock_logger.error.call_args_list]
            self.assertTrue(any("does not support sleep" in msg for msg in error_messages))
            self.assertTrue(any("does not support wake_up" in msg for msg in error_messages))

    def test_patch_vllm_lifespan_trigger_failure_handled_gracefully(self):
        mock_app = MagicMock()
        mock_router = MagicMock()

        @asynccontextmanager
        async def mock_original_lifespan(app):
            yield {"status": "ok"}

        mock_router.lifespan_context = mock_original_lifespan
        mock_app.router = mock_router
        mock_engine = AsyncMock()
        mock_engine.sleep = AsyncMock()
        mock_engine.wake_up = AsyncMock()
        mock_state = MagicMock(spec=["engine_client"])
        mock_state.engine_client = mock_engine
        mock_app.state = mock_state

        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.trigger.side_effect = SnapshotError("Checkpoint trigger failed")

        with patch("docker.scripts.snapshot.vllm.wrapper.logger") as mock_logger:
            patched_app = patch_vllm_lifespan(mock_app, snapshot_provider=mock_provider)
            async def run_test():
                async with mock_router.lifespan_context(mock_app) as state:
                    self.assertEqual(state, {"status": "ok"})
            asyncio.run(run_test())

            mock_logger.error.assert_called()
            errors = [call[0][0] for call in mock_logger.error.call_args_list]
            self.assertTrue(any("Snapshot checkpointing failed" in e for e in errors))
            mock_engine.wake_up.assert_called_once()

    @patch("docker.scripts.snapshot.providers.GKESnapshotProvider")
    def test_patch_vllm_lifespan_default_provider_unset(self, mock_provider_cls):
        mock_provider_inst = MagicMock()
        mock_provider_cls.return_value = mock_provider_inst

        mock_app = MagicMock()
        mock_router = MagicMock()

        @asynccontextmanager
        async def mock_original_lifespan(app):
            yield {"status": "ok"}

        mock_router.lifespan_context = mock_original_lifespan
        mock_app.router = mock_router
        mock_engine = AsyncMock()
        mock_engine.sleep = AsyncMock()
        mock_engine.wake_up = AsyncMock()
        mock_app.state = MagicMock(engine_client=mock_engine)

        with patch.dict(os.environ, {}, clear=True):
            patched_app = patch_vllm_lifespan(mock_app, snapshot_provider=None)
            self.assertEqual(patched_app.router.lifespan_context, mock_original_lifespan)
            async def run_test():
                async with mock_router.lifespan_context(mock_app) as state:
                    self.assertEqual(state, {"status": "ok"})
            asyncio.run(run_test())

        mock_provider_cls.assert_not_called()
        mock_provider_inst.trigger.assert_not_called()
        mock_engine.sleep.assert_not_called()
        mock_engine.wake_up.assert_not_called()

    @patch("docker.scripts.snapshot.providers.GKESnapshotProvider")
    def test_patch_vllm_lifespan_provider_gke_gvisor(self, mock_provider_cls):
        mock_provider_inst = MagicMock()
        mock_provider_cls.return_value = mock_provider_inst

        mock_app = MagicMock()
        mock_router = MagicMock()

        @asynccontextmanager
        async def mock_original_lifespan(app):
            yield {"status": "ok"}

        mock_router.lifespan_context = mock_original_lifespan
        mock_app.router = mock_router
        mock_app.state = MagicMock(engine_client=AsyncMock())

        with patch.dict(os.environ, {"SNAPSHOT_PROVIDER": "gke_gvisor"}, clear=True):
            patched_app = patch_vllm_lifespan(mock_app, snapshot_provider=None)
            async def run_test():
                async with mock_router.lifespan_context(mock_app) as state:
                    self.assertEqual(state, {"status": "ok"})
            asyncio.run(run_test())

        mock_provider_cls.assert_called_once()
        mock_provider_inst.trigger.assert_called_once()


class TestGetSnapshotProvider(unittest.TestCase):
    def test_default_unset_env_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = get_snapshot_provider()
            self.assertIsNone(provider)

    def test_empty_env_returns_none(self):
        with patch.dict(os.environ, {"SNAPSHOT_PROVIDER": ""}):
            provider = get_snapshot_provider()
            self.assertIsNone(provider)

    def test_whitespace_env_returns_none(self):
        with patch.dict(os.environ, {"SNAPSHOT_PROVIDER": "   "}):
            provider = get_snapshot_provider()
            self.assertIsNone(provider)

    def test_valid_gke_keywords_return_gke(self):
        for keyword in ("gke_gvisor", "GKE_GVISOR"):
            with patch.dict(os.environ, {"SNAPSHOT_PROVIDER": keyword}):
                provider = get_snapshot_provider()
                self.assertIsInstance(provider, GKESnapshotProvider)

    def test_disabled_or_unknown_keywords_return_none(self):
        for keyword in ("gke", "GKE", "none", "disabled", "false", "0", "aws", "other", "gke_sandbox", "gvisor"):
            with patch.dict(os.environ, {"SNAPSHOT_PROVIDER": keyword}):
                provider = get_snapshot_provider()
                self.assertIsNone(provider)


class TestLauncher(unittest.TestCase):
    @patch("docker.scripts.snapshot.launcher.patch_vllm_lifespan")
    def test_build_app_hooks_and_patches_lifespan(self, mock_patch_lifespan):
        from docker.scripts.snapshot import launcher

        mock_app = MagicMock()
        mock_orig_build_app = MagicMock(return_value=mock_app)
        mock_patch_lifespan.return_value = "patched_app"

        wrapped_build_app = lambda *args, **kwargs: launcher.patch_vllm_lifespan(mock_orig_build_app(*args, **kwargs))
        result = wrapped_build_app("arg1", key="val")

        mock_orig_build_app.assert_called_once_with("arg1", key="val")
        mock_patch_lifespan.assert_called_once_with(mock_app)
        self.assertEqual(result, "patched_app")

    @patch("sys.argv", ["launcher.py", "--model", "meta-llama/Llama-2-7b"])
    def test_launcher_main(self):
        import sys
        from docker.scripts.snapshot import launcher

        mock_vllm_main = MagicMock()
        with patch.dict("sys.modules", {"vllm.entrypoints.cli.main": MagicMock(main=mock_vllm_main)}):
            launcher.main()
            self.assertEqual(sys.argv, ["vllm", "serve", "--model", "meta-llama/Llama-2-7b"])
            mock_vllm_main.assert_called_once()

    def test_launcher_main_vllm_not_installed_raises(self):
        from docker.scripts.snapshot import launcher

        with patch.dict("sys.modules", {"vllm.entrypoints.cli.main": None}):
            with self.assertRaises(RuntimeError) as ctx:
                launcher.main()
            self.assertIn("vLLM must be installed to run snapshot launcher", str(ctx.exception))

    def test_launcher_api_server_not_importable_warning(self):
        from docker.scripts.snapshot import launcher

        with patch.dict("sys.modules", {"vllm.entrypoints.openai.api_server": None}), \
             patch("docker.scripts.snapshot.launcher.logger") as mock_logger:
            result = launcher._hook_api_server()
            self.assertFalse(result)
            mock_logger.warning.assert_called_once()
            self.assertIn("vLLM API server is not importable", mock_logger.warning.call_args[0][0])

    def test_launcher_hook_api_server_success(self):
        from docker.scripts.snapshot import launcher

        mock_api_server = MagicMock()
        mock_api_server.build_app = MagicMock(return_value="app")
        modules = {
            "vllm": MagicMock(),
            "vllm.entrypoints": MagicMock(),
            "vllm.entrypoints.openai": MagicMock(api_server=mock_api_server),
            "vllm.entrypoints.openai.api_server": mock_api_server,
        }
        with patch.dict("sys.modules", modules):
            result = launcher._hook_api_server()
            self.assertTrue(result)
            self.assertIsNotNone(mock_api_server.build_app)


if __name__ == "__main__":
    unittest.main()

