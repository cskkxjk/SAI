"""Microphone ownership and release tests; no real audio is recorded."""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

from config_client import ClientConfig
from core.client.audio.stream import AudioStreamManager
from core.client.manager.mic_runner import MicRunner
from core.client.shortcut.task import ShortcutTask
from core.client.state import ClientState


class MicrophoneLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = SimpleNamespace(state=ClientState(), loop=asyncio.get_running_loop())
        self.app.stream = AudioStreamManager(self.app)
        self.app.stream.keep_open = False
        self.events = []
        self.cancelled = False

        async def record():
            try:
                while True:
                    item = await self.app.state.queue_in.get()
                    self.app.state.queue_in.task_done()
                    self.events.append(item["type"])
                    if item["type"] == "finish":
                        return
            except asyncio.CancelledError:
                self.cancelled = True
                raise

        self.recorder = SimpleNamespace(record_and_send=record)
        self.shortcut = SimpleNamespace(key="caps_lock", suppress=True,
                                        is_toggle_key=lambda: True)
        self.task = ShortcutTask(self.app, self.shortcut, lambda app: self.recorder)
        self.task._status = Mock()
        self.patches = [
            patch("core.client.audio.stream.resolve_capture_device", return_value=3),
            patch("core.client.audio.stream.sd.query_devices",
                  return_value={"name": "Test mic", "max_input_channels": 1}),
            patch("core.client.audio.stream.sd.InputStream"),
            patch("core.client.ui.toast"),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.hardware = self.mocks[2].return_value
        self.toast = self.mocks[3]

    async def asyncTearDown(self):
        if self.task.is_recording:
            self.task.cancel()
        if self.task.task is not None:
            await asyncio.wait_for(asyncio.wrap_future(self.task.task), 3)
        self.app.stream.shutdown()
        for p in reversed(self.patches):
            p.stop()

    async def wait_open(self):
        for _ in range(300):
            if self.app.stream._running:
                return
            await asyncio.sleep(.01)
        self.fail("Stream did not open")

    async def wait_done(self):
        await asyncio.wait_for(asyncio.wrap_future(self.task.task), 3)
        self.assertFalse(self.app.state.recording)
        self.assertEqual(
            self.app.stream._running,
            self.app.stream.keep_open and self.app.state.stream is not None)
        self.assertFalse(self.app.stream.session_lock.locked())

    async def test_startup_listens_without_opening_microphone(self):
        app = Mock()
        with patch("core.client.manager.mic_runner.TipsDisplay"), \
             patch.object(ClientConfig, "udp_control", False):
            MicRunner(app).start_resources()
        app.shortcut.start.assert_called_once()
        app.stream.start.assert_not_called()

    async def test_hold_release_closes_hardware_and_finishes_transcript(self):
        self.task.launch()
        await self.wait_open()
        self.task.finish()
        await self.wait_done()
        self.hardware.close.assert_called_once()
        self.assertEqual(self.events, ["begin", "finish"])
        self.assertFalse(self.cancelled)

    async def test_short_press_closes_hardware_without_transcript(self):
        self.task.launch()
        await self.wait_open()
        self.task.cancel()
        await self.wait_done()
        self.hardware.close.assert_called_once()
        self.assertNotIn("finish", self.events)
        self.assertTrue(self.cancelled)

    async def test_release_before_session_runs_never_opens_hardware(self):
        self.task.launch()
        self.task.cancel()
        await self.wait_done()
        self.mocks[2].assert_not_called()

    async def test_release_during_driver_open_closes_after_open(self):
        entered = threading.Event()
        proceed = threading.Event()

        def slow_start():
            entered.set()
            if not proceed.wait(3):
                raise TimeoutError("Test did not release fake driver")

        self.hardware.start.side_effect = slow_start
        self.task.launch()
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.task.finish()
        finally:
            proceed.set()
        await self.wait_done()
        self.hardware.close.assert_called_once()

    async def test_open_failure_releases_device_and_allows_retry(self):
        self.hardware.start.side_effect = RuntimeError("Driver failed")
        self.task.launch()
        await self.wait_done()
        self.hardware.close.assert_called_once()
        self.toast.assert_called_once()
        self.hardware.start.side_effect = None
        self.task.launch()
        await self.wait_open()
        self.task.finish()
        await self.wait_done()
        self.assertEqual(self.hardware.close.call_count, 2)

    async def test_missing_device_does_not_leave_recording_state(self):
        self.mocks[0].side_effect = ValueError("Device unplugged")
        self.task.launch()
        await self.wait_done()
        self.mocks[2].assert_not_called()
        self.toast.assert_called_once()

    async def test_other_shortcut_cannot_close_active_recording(self):
        other = ShortcutTask(self.app, self.shortcut, lambda app: self.recorder)
        self.task.launch()
        await self.wait_open()
        other.launch()
        other.finish()
        other.cancel()
        self.assertFalse(other.is_recording)
        self.assertTrue(self.task.is_recording)
        self.hardware.close.assert_not_called()
        self.task.finish()
        await self.wait_done()

    async def test_next_recording_can_open_after_previous_release(self):
        for _ in range(2):
            self.task.launch()
            await self.wait_open()
            self.task.finish()
            await self.wait_done()
        self.assertEqual(self.hardware.start.call_count, 2)
        self.assertEqual(self.hardware.close.call_count, 2)
        self.assertEqual(self.events, ["begin", "finish", "begin", "finish"])

    async def test_real_recorder_sends_audio_and_final_after_release(self):
        from core.client.audio.recorder import AudioRecorder
        recorder = AudioRecorder(self.app)
        recorder._send_message = AsyncMock()
        self.task._recorder_class = lambda app: recorder
        with patch.object(ClientConfig, "save_audio", False), \
             patch.object(ClientConfig, "threshold", 0):
            self.task.launch()
            await self.wait_open()
            self.app.stream._audio_callback(
                np.ones((2400, 1), dtype=np.float32), 2400, None, None)
            for _ in range(100):
                if recorder._send_message.await_count:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(recorder._send_message.await_count, 1)
            self.task.finish()
            await self.wait_done()
        messages = [call.args[0] for call in recorder._send_message.await_args_list]
        self.assertEqual([message.is_final for message in messages], [False, True])
        self.assertTrue(messages[0].data)
        self.hardware.close.assert_called_once()

    async def test_reopen_and_late_callback_do_not_open_idle_device(self):
        self.app.stream.reopen()
        self.app.stream._on_stream_finished()
        await asyncio.sleep(0)
        self.mocks[2].assert_not_called()

    async def test_shutdown_blocks_pending_open(self):
        self.app.stream.shutdown()
        self.task.launch()
        await self.wait_done()
        self.mocks[2].assert_not_called()

    async def test_f8_toggle_uses_same_open_close_lifecycle(self):
        from core.client.shortcut.event_handler import ShortcutEventHandler
        self.task.threshold = .3
        self.task.shortcut.key = "f8"
        self.task.event.set()
        handler = ShortcutEventHandler({}, Mock(), Mock())
        handler._manage_task(self.task)
        await self.wait_open()
        self.assertTrue(self.task.is_recording)
        handler._manage_task(self.task)
        await self.wait_done()
        self.hardware.close.assert_called_once()

    async def test_fast_mode_keeps_stream_but_discards_idle_samples(self):
        self.app.stream.keep_open = True
        def supply_audio():
            self.app.stream._audio_callback(
                np.ones((960, 1), dtype=np.float32), 960, None, None)

        self.hardware.start.side_effect = supply_audio
        self.assertIs(await asyncio.to_thread(self.app.stream.prepare), self.hardware)
        self.hardware.stop.assert_not_called()
        self.assertTrue(self.app.stream._running)
        self.assertTrue(self.app.state.queue_in.empty())
        self.hardware.close.assert_not_called()
        self.task.launch()
        await asyncio.sleep(.03)
        self.task.finish()
        await self.wait_done()
        self.mocks[2].assert_called_once()
        self.hardware.start.assert_called_once()
        self.hardware.close.assert_not_called()
        self.events.clear()
        supply_audio()
        await asyncio.sleep(0)
        self.assertTrue(self.app.state.queue_in.empty())
        self.app.stream.shutdown()
        self.hardware.close.assert_called_once()

    async def test_prepare_silence_has_bounded_wait(self):
        self.app.stream.keep_open = True
        with patch.object(self.app.stream, "WARMUP_TIMEOUT", .01):
            await asyncio.to_thread(self.app.stream.prepare)
        self.assertTrue(self.app.stream._running)
        self.assertTrue(self.app.state.queue_in.empty())

    async def test_prepare_is_noop_without_explicit_fast_mode(self):
        self.assertIsNone(self.app.stream.prepare())
        self.mocks[2].assert_not_called()

    async def test_startup_only_prepares_fast_mode(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                app = Mock()
                with patch.object(ClientConfig, "keep_microphone_open", enabled), \
                     patch.dict("os.environ", {"SAI_READY_FILE": ""}), \
                     patch("core.client.manager.mic_runner.TipsDisplay"), \
                     patch("core.client.output.ResultProcessor") as processor:
                    processor.return_value.start = AsyncMock()
                    await MicRunner(app).run()
                    self.assertEqual(app.stream.prepare.call_count, int(enabled))


class AudioThreadTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.app = SimpleNamespace(state=ClientState())
        self.manager = AudioStreamManager(self.app)
        self.manager.keep_open = False
        self.ole = Mock()
        self.ole.CoInitializeEx.side_effect = lambda *_: self.note("init") or 0
        self.ole.CoUninitialize.side_effect = lambda: self.note("uninit")
        self.hardware = Mock()
        self.hardware.start.side_effect = lambda: self.note("start")
        self.hardware.close.side_effect = lambda: self.note("close")
        patches = [
            patch("core.client.audio.stream.sys.platform", "win32"),
            patch("core.client.audio.stream.ctypes.WinDLL", return_value=self.ole,
                  create=True),
            patch("core.client.audio.stream.resolve_capture_device", return_value=3),
            patch("core.client.audio.stream.sd.query_devices",
                  return_value={"name": "Test mic", "max_input_channels": 1}),
            patch("core.client.audio.stream.sd.InputStream", return_value=self.hardware),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.manager.shutdown)

    def note(self, name):
        self.events.append((name, threading.get_ident()))

    def test_stream_lifetime_and_com_share_one_noncaller_thread(self):
        for _ in range(2):
            self.assertIs(self.manager.start(), self.hardware)
            self.manager.stop()
        self.manager.shutdown()
        self.manager.shutdown()
        self.assertEqual([name for name, _ in self.events],
                         ["init", "start", "close", "uninit"] * 2)
        owners = {owner for _, owner in self.events}
        self.assertEqual(len(owners), 1)
        self.assertNotIn(threading.get_ident(), owners)
        self.assertIsNone(self.manager.start())

    def test_fast_mode_keeps_com_until_stream_is_closed(self):
        self.manager.keep_open = True
        self.manager.start()
        self.manager.stop()
        self.ole.CoUninitialize.assert_not_called()
        self.hardware.close.assert_not_called()
        self.manager.shutdown()
        self.assertEqual([name for name, _ in self.events],
                         ["init", "start", "close", "uninit"])

    def test_failed_start_closes_stream_before_com_cleanup_and_can_retry(self):
        self.hardware.start.side_effect = RuntimeError("Driver failed")
        self.assertIsNone(self.manager.start())
        self.assertEqual([name for name, _ in self.events], ["init", "close", "uninit"])
        self.hardware.start.side_effect = lambda: self.note("start")
        self.assertIs(self.manager.start(), self.hardware)
        self.manager.stop()
        self.assertEqual([name for name, _ in self.events][-4:],
                         ["init", "start", "close", "uninit"])

    def test_successful_com_reference_is_balanced_even_for_s_false(self):
        self.ole.CoInitializeEx.return_value = 1
        self.ole.CoInitializeEx.side_effect = None
        self.manager.start()
        self.manager.shutdown()
        self.ole.CoUninitialize.assert_called_once()

    def test_com_initialization_failure_does_not_open_or_uninitialize(self):
        self.ole.CoInitializeEx.side_effect = None
        self.ole.CoInitializeEx.return_value = -2147417850
        with self.assertRaisesRegex(OSError, "0x80010106"):
            self.manager.start()
        self.hardware.start.assert_not_called()
        self.manager.shutdown()
        self.ole.CoUninitialize.assert_not_called()

    def test_non_windows_does_not_load_com(self):
        with patch("core.client.audio.stream.sys.platform", "linux"), \
             patch("core.client.audio.stream.ctypes.WinDLL", create=True) as dll:
            self.manager.start()
            self.manager.stop()
            dll.assert_not_called()
