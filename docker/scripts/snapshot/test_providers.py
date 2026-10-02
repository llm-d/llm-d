"""
Unit tests for GKE Snapshot Providers, Cache Clearing, vLLM Lifespan Wrapper, and SGLang Wrapper.
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


class TestSGLangWrapper(unittest.TestCase):
    def _make_mock_http_server(self):
        mock_http_server = MagicMock()
        mock_http_server.ServerStatus = MagicMock(Starting="Starting", Up="Up")
        mock_http_server._wait_and_warmup = MagicMock(return_value="orig_warmup")
        mock_http_server._execute_server_warmup = MagicMock(return_value=True)
        mock_http_server._freeze_gc_after_server_warmup = MagicMock()
        mock_http_server._wait_weights_ready = MagicMock()
        mock_http_server.get_model.return_value = MagicMock(
            checkpoint_engine_wait_weights_before_ready=False
        )
        mock_http_server.get_exec.return_value = MagicMock(
            moe=MagicMock(spec=["ep_join_mode"], ep_join_mode=None)
        )
        mock_http_server.get_serving.return_value = MagicMock(skip_server_warmup=False)
        mock_http_server.get_observability.return_value = MagicMock(
            debug_tensor_dump_input_file=None
        )
        mock_http_server.kill_process_tree = MagicMock()
        mock_http_server._global_state = MagicMock(tokenizer_manager=MagicMock())
        return mock_http_server

    def setUp(self):
        patcher = patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: MagicMock(
                result=lambda: asyncio.run(coro) if asyncio.iscoroutine(coro) else coro
            ),
        )
        self.mock_run_threadsafe = patcher.start()
        self.addCleanup(patcher.stop)

    def _make_sglang_modules(self, mock_http_server):
        mock_io_struct = MagicMock()
        mock_io_struct.ReleaseMemoryOccupationReqInput.side_effect = (
            lambda tags=None: {"type": "release", "tags": tags}
        )
        mock_io_struct.ResumeMemoryOccupationReqInput.side_effect = (
            lambda tags=None: {"type": "resume", "tags": tags}
        )
        return {
            "sglang": MagicMock(),
            "sglang.srt": MagicMock(),
            "sglang.srt.entrypoints": MagicMock(http_server=mock_http_server),
            "sglang.srt.entrypoints.http_server": mock_http_server,
            "sglang.srt.managers": MagicMock(io_struct=mock_io_struct),
            "sglang.srt.managers.io_struct": mock_io_struct,
        }

    def test_patch_sglang_wait_and_warmup_disabled_provider(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        orig_wait = mock_http_server._wait_and_warmup
        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules), patch.dict(
            os.environ, {"SNAPSHOT_PROVIDER": ""}, clear=True
        ):
            patch_sglang_wait_and_warmup(snapshot_provider=None)
            self.assertEqual(mock_http_server._wait_and_warmup, orig_wait)

    def test_patch_sglang_wait_and_warmup_sleep_trigger_wake(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        orig_wait = mock_http_server._wait_and_warmup
        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = True
        mock_callback = MagicMock()

        events = []
        status_during_phases = {}
        tokenizer_mgr = mock_http_server._global_state.tokenizer_manager
        mock_http_server._execute_server_warmup.side_effect = (
            lambda *a, **kw: events.append("warmup") or True
        )
        mock_http_server._freeze_gc_after_server_warmup.side_effect = (
            lambda *a, **kw: events.append("freeze_gc")
        )

        async def _async_release(req):
            status_during_phases["release"] = tokenizer_mgr.server_status
            events.append("release")

        def _trigger():
            status_during_phases["trigger"] = tokenizer_mgr.server_status
            events.append("trigger")

        async def _async_resume(req):
            status_during_phases["resume"] = tokenizer_mgr.server_status
            events.append("resume")

        tokenizer_mgr.release_memory_occupation.side_effect = _async_release
        mock_provider.trigger.side_effect = _trigger
        tokenizer_mgr.resume_memory_occupation.side_effect = _async_resume
        mock_callback.side_effect = lambda: events.append("callback")

        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules):
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            self.assertNotEqual(mock_http_server._wait_and_warmup, orig_wait)

            mock_http_server._wait_and_warmup(
                MagicMock(is_ep_scale_joiner=False),
                launch_callback=mock_callback,
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

        self.assertEqual(
            events,
            ["warmup", "freeze_gc", "release", "trigger", "resume", "callback"],
        )
        self.assertEqual(
            status_during_phases,
            {
                "release": mock_http_server.ServerStatus.Starting,
                "trigger": mock_http_server.ServerStatus.Starting,
                "resume": mock_http_server.ServerStatus.Starting,
            },
        )
        self.assertEqual(self.mock_run_threadsafe.call_count, 2)
        for call in self.mock_run_threadsafe.call_args_list:
            self.assertEqual(call[0][1], tokenizer_mgr.event_loop)
        tokenizer_mgr.release_memory_occupation.assert_called_once_with(
            {"type": "release", "tags": ["weights", "kv_cache"]}
        )
        mock_provider.trigger.assert_called_once()
        tokenizer_mgr.resume_memory_occupation.assert_called_once_with(
            {"type": "resume", "tags": ["weights", "kv_cache"]}
        )
        self.assertEqual(tokenizer_mgr.server_status, mock_http_server.ServerStatus.Up)
        mock_callback.assert_called_once()

    def test_patch_sglang_wait_and_warmup_wait_weights_ready(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        mock_http_server.get_model.return_value.checkpoint_engine_wait_weights_before_ready = (
            True
        )
        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = True

        events = []
        mock_http_server._wait_weights_ready.side_effect = lambda: events.append(
            "wait_weights"
        )
        mock_http_server._execute_server_warmup.side_effect = (
            lambda *a, **kw: events.append("warmup") or True
        )

        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules):
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            mock_http_server._wait_and_warmup(
                MagicMock(is_ep_scale_joiner=False),
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

        mock_http_server._wait_weights_ready.assert_called_once()
        self.assertEqual(events, ["wait_weights", "warmup"])
        mock_provider.trigger.assert_called_once()

    def test_patch_sglang_wait_and_warmup_skip_server_warmup(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        mock_http_server.get_serving.return_value.skip_server_warmup = True
        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = True

        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules), patch(
            "docker.scripts.snapshot.sglang.wrapper.logger"
        ) as mock_logger:
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            mock_http_server._wait_and_warmup(
                MagicMock(is_ep_scale_joiner=False),
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

            mock_logger.warning.assert_called_once_with(
                "[Control Plane] Warmup skipped."
            )

        mock_http_server._execute_server_warmup.assert_not_called()
        mock_http_server._freeze_gc_after_server_warmup.assert_called_once()
        mock_provider.trigger.assert_called_once()

    def test_patch_sglang_wait_and_warmup_elastic_ep_scale_joiner_skips_warmup(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        mock_http_server.get_exec.return_value.moe.ep_join_mode = "scale"
        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = True

        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules):
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            mock_http_server._wait_and_warmup(
                MagicMock(is_ep_scale_joiner=True),
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

        mock_http_server._execute_server_warmup.assert_not_called()
        mock_provider.trigger.assert_called_once()

    def test_patch_sglang_wait_and_warmup_debug_tensor_dump_kills_process_tree(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        mock_http_server.get_observability.return_value.debug_tensor_dump_input_file = (
            "/tmp/dump.pt"
        )
        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = True

        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules), patch(
            "os.getpid", return_value=9999
        ):
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            mock_http_server._wait_and_warmup(
                MagicMock(is_ep_scale_joiner=False),
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

        mock_http_server.kill_process_tree.assert_called_once_with(9999)

    def test_patch_sglang_wait_and_warmup_trigger_failure_handled_gracefully(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = True
        mock_provider.trigger.side_effect = SnapshotError("Checkpoint trigger failed")
        mock_callback = MagicMock()

        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules), patch(
            "docker.scripts.snapshot.sglang.wrapper.logger"
        ) as mock_logger:
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            mock_http_server._wait_and_warmup(
                MagicMock(is_ep_scale_joiner=False),
                launch_callback=mock_callback,
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

            mock_logger.error.assert_called_once()
            self.assertIn(
                "Snapshot checkpointing failed",
                mock_logger.error.call_args[0][0],
            )

        tokenizer_mgr = mock_http_server._global_state.tokenizer_manager
        tokenizer_mgr.release_memory_occupation.assert_called_once_with(
            {"type": "release", "tags": ["weights", "kv_cache"]}
        )
        tokenizer_mgr.resume_memory_occupation.assert_called_once_with(
            {"type": "resume", "tags": ["weights", "kv_cache"]}
        )
        self.assertEqual(tokenizer_mgr.server_status, mock_http_server.ServerStatus.Up)
        mock_callback.assert_called_once()

    def test_patch_sglang_wait_and_warmup_warmup_failure_aborts_before_snapshot(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        mock_http_server._execute_server_warmup.return_value = False
        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = True
        mock_callback = MagicMock()

        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules):
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            mock_http_server._wait_and_warmup(
                MagicMock(is_ep_scale_joiner=False),
                launch_callback=mock_callback,
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

        tokenizer_mgr = mock_http_server._global_state.tokenizer_manager
        mock_http_server._freeze_gc_after_server_warmup.assert_not_called()
        tokenizer_mgr.release_memory_occupation.assert_not_called()
        mock_provider.trigger.assert_not_called()
        tokenizer_mgr.resume_memory_occupation.assert_not_called()
        self.assertNotEqual(
            tokenizer_mgr.server_status, mock_http_server.ServerStatus.Up
        )
        mock_callback.assert_not_called()

    @patch("docker.scripts.snapshot.providers.GKESnapshotProvider")
    def test_patch_sglang_wait_and_warmup_provider_gke_gvisor(self, mock_provider_cls):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_provider_inst = MagicMock()
        mock_provider_inst.is_available.return_value = True
        mock_provider_cls.return_value = mock_provider_inst

        mock_http_server = self._make_mock_http_server()
        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules), patch.dict(
            os.environ, {"SNAPSHOT_PROVIDER": "gke_gvisor"}, clear=True
        ):
            patch_sglang_wait_and_warmup(snapshot_provider=None)
            mock_http_server._wait_and_warmup(
                MagicMock(is_ep_scale_joiner=False),
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

        mock_provider_cls.assert_called_once()
        mock_provider_inst.trigger.assert_called_once()

    def test_patch_sglang_wait_and_warmup_provider_not_available_falls_back(self):
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        mock_http_server = self._make_mock_http_server()
        orig_wait = mock_http_server._wait_and_warmup
        mock_provider = MagicMock(spec=GKESnapshotProvider)
        mock_provider.is_available.return_value = False
        mock_provider.proc_path = CHECKPOINT_PATH
        mock_callback = MagicMock()

        modules = self._make_sglang_modules(mock_http_server)
        with patch.dict("sys.modules", modules), patch(
            "docker.scripts.snapshot.sglang.wrapper.logger"
        ) as mock_logger:
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            server_args = MagicMock(is_ep_scale_joiner=False)
            result = mock_http_server._wait_and_warmup(
                server_args,
                launch_callback=mock_callback,
                execute_warmup_func=mock_http_server._execute_server_warmup,
            )

            mock_logger.warning.assert_called_once()
            self.assertIn(
                "Pod snapshot trigger not available",
                mock_logger.warning.call_args[0][0],
            )

        self.assertEqual(result, "orig_warmup")
        orig_wait.assert_called_once_with(
            server_args,
            launch_callback=mock_callback,
            execute_warmup_func=mock_http_server._execute_server_warmup,
        )
        tokenizer_mgr = mock_http_server._global_state.tokenizer_manager
        tokenizer_mgr.release_memory_occupation.assert_not_called()
        mock_provider.trigger.assert_not_called()
        tokenizer_mgr.resume_memory_occupation.assert_not_called()


class TestSGLangLauncher(unittest.TestCase):
    def test_sglang_launcher_api_server_not_importable_warning(self):
        from docker.scripts.snapshot.sglang import launcher as sglang_launcher

        with patch.dict("sys.modules", {"sglang": None, "sglang.srt": None}), patch(
            "docker.scripts.snapshot.sglang.launcher.logger"
        ) as mock_logger:
            result = sglang_launcher._hook_api_server()
            self.assertFalse(result)
            mock_logger.warning.assert_called_once()
            self.assertIn(
                "SGLang server is not importable",
                mock_logger.warning.call_args[0][0],
            )

    @patch("docker.scripts.snapshot.sglang.wrapper.patch_sglang_wait_and_warmup")
    def test_sglang_launcher_hook_api_server_success(self, mock_patch):
        from docker.scripts.snapshot.sglang import launcher as sglang_launcher

        result = sglang_launcher._hook_api_server()
        self.assertTrue(result)
        mock_patch.assert_called_once()

    @patch("sys.argv", ["launcher.py", "--model-path", "Qwen/Qwen3-32B"])
    def test_sglang_launcher_main(self):
        from docker.scripts.snapshot.sglang import launcher as sglang_launcher

        mock_launch_server = MagicMock()
        mock_prepare_args = MagicMock(return_value="parsed_args")
        modules = {
            "sglang": MagicMock(),
            "sglang.srt": MagicMock(),
            "sglang.srt.entrypoints": MagicMock(),
            "sglang.srt.entrypoints.http_server": MagicMock(
                launch_server=mock_launch_server
            ),
            "sglang.srt.server_args": MagicMock(
                prepare_server_args=mock_prepare_args
            ),
        }
        with patch.dict("sys.modules", modules):
            sglang_launcher.main()

        mock_prepare_args.assert_called_once_with(["--model-path", "Qwen/Qwen3-32B"])
        mock_launch_server.assert_called_once_with("parsed_args")

    def test_sglang_launcher_main_not_installed_raises(self):
        from docker.scripts.snapshot.sglang import launcher as sglang_launcher

        with patch.dict(
            "sys.modules", {"sglang.srt.entrypoints.http_server": None}
        ):
            with self.assertRaises(RuntimeError) as ctx:
                sglang_launcher.main()
            self.assertIn(
                "sglang must be installed to run snapshot launcher",
                str(ctx.exception),
            )


class TestSnapshotImports(unittest.TestCase):
    def test_snapshot_package_and_subpackage_exports(self):
        import importlib

        snapshot_pkg = importlib.import_module("docker.scripts.snapshot")
        sglang_pkg = importlib.import_module("docker.scripts.snapshot.sglang")
        vllm_pkg = importlib.import_module("docker.scripts.snapshot.vllm")
        sglang_wrapper = importlib.import_module(
            "docker.scripts.snapshot.sglang.wrapper"
        )
        sglang_launcher = importlib.import_module(
            "docker.scripts.snapshot.sglang.launcher"
        )

        expected_top_level = {
            "GKESnapshotProvider",
            "SnapshotError",
            "get_snapshot_provider",
            "patch_vllm_lifespan",
            "patch_sglang_wait_and_warmup",
        }
        self.assertTrue(expected_top_level.issubset(set(snapshot_pkg.__all__)))
        for name in expected_top_level:
            self.assertTrue(hasattr(snapshot_pkg, name), f"Missing export {name}")

        self.assertEqual(sglang_pkg.__all__, ["patch_sglang_wait_and_warmup"])
        self.assertIs(
            sglang_pkg.patch_sglang_wait_and_warmup,
            sglang_wrapper.patch_sglang_wait_and_warmup,
        )
        self.assertEqual(vllm_pkg.__all__, ["patch_vllm_lifespan"])
        self.assertTrue(callable(sglang_launcher.main))
        self.assertTrue(callable(sglang_launcher._hook_api_server))

    def test_snapshot_init_loads_when_only_sglang_or_vllm_mounted(self):
        import importlib
        import docker.scripts.snapshot as snapshot_pkg

        try:
            # Simulate SGLang pod where docker/scripts/snapshot/vllm ConfigMap is not mounted
            with patch.dict("sys.modules", {"docker.scripts.snapshot.vllm": None}):
                importlib.reload(snapshot_pkg)
                self.assertIn("patch_sglang_wait_and_warmup", snapshot_pkg.__all__)
                self.assertNotIn("patch_vllm_lifespan", snapshot_pkg.__all__)

            # Simulate vLLM pod where docker/scripts/snapshot/sglang ConfigMap is not mounted
            with patch.dict("sys.modules", {"docker.scripts.snapshot.sglang": None}):
                importlib.reload(snapshot_pkg)
                self.assertIn("patch_vllm_lifespan", snapshot_pkg.__all__)
                self.assertNotIn("patch_sglang_wait_and_warmup", snapshot_pkg.__all__)
        finally:
            importlib.reload(snapshot_pkg)

    def test_sglang_wrapper_strict_module_imports(self):
        import types
        from docker.scripts.snapshot.sglang.wrapper import patch_sglang_wait_and_warmup

        http_server_mod = types.ModuleType("sglang.srt.entrypoints.http_server")
        for attr in (
            "ServerStatus",
            "_wait_and_warmup",
            "_execute_server_warmup",
            "_freeze_gc_after_server_warmup",
            "_wait_weights_ready",
            "get_exec",
            "get_model",
            "get_observability",
            "get_serving",
            "kill_process_tree",
        ):
            setattr(http_server_mod, attr, MagicMock(name=attr))

        io_struct_mod = types.ModuleType("sglang.srt.managers.io_struct")
        for attr in (
            "ReleaseMemoryOccupationReqInput",
            "ResumeMemoryOccupationReqInput",
        ):
            setattr(io_struct_mod, attr, MagicMock(name=attr))

        entrypoints_mod = types.ModuleType("sglang.srt.entrypoints")
        entrypoints_mod.http_server = http_server_mod
        managers_mod = types.ModuleType("sglang.srt.managers")
        managers_mod.io_struct = io_struct_mod
        srt_mod = types.ModuleType("sglang.srt")
        srt_mod.entrypoints = entrypoints_mod
        srt_mod.managers = managers_mod
        sglang_mod = types.ModuleType("sglang")
        sglang_mod.srt = srt_mod

        strict_modules = {
            "sglang": sglang_mod,
            "sglang.srt": srt_mod,
            "sglang.srt.entrypoints": entrypoints_mod,
            "sglang.srt.entrypoints.http_server": http_server_mod,
            "sglang.srt.managers": managers_mod,
            "sglang.srt.managers.io_struct": io_struct_mod,
        }

        mock_provider = MagicMock(spec=GKESnapshotProvider)
        with patch.dict("sys.modules", strict_modules):
            patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)
            self.assertTrue(callable(http_server_mod._wait_and_warmup))

        # Verify missing required symbol raises ImportError
        delattr(io_struct_mod, "ReleaseMemoryOccupationReqInput")
        with patch.dict("sys.modules", strict_modules):
            with self.assertRaises(ImportError):
                patch_sglang_wait_and_warmup(snapshot_provider=mock_provider)


if __name__ == "__main__":
    unittest.main()
