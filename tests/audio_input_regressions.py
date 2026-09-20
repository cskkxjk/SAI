"""Audio integrity, default endpoint choice, and empty-recognition protection."""

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

from config_client import ClientConfig
from core.audio_devices import resolve_capture_device
from core.client.audio.recorder import AudioRecorder
from core.client.output.result_processor import ResultProcessor
from core.client.state import ClientState


class EndpointTests(unittest.TestCase):
    def test_default_uses_matching_wasapi_endpoint(self):
        devices = [
            {"index": 3, "name": "Other mic", "hostapi": "Windows WASAPI"},
            {"index": 15, "name": "USB mic", "hostapi": "Windows WASAPI"},
        ]
        with patch("core.audio_devices.resolve_input_device", return_value=None), \
             patch("core.audio_devices.sd.query_devices", return_value={"name": "USB mic"}), \
             patch("core.audio_devices.input_devices", return_value=devices):
            self.assertEqual(resolve_capture_device(None), 15)

    def test_default_never_switches_to_an_unrelated_or_ambiguous_mic(self):
        for devices in (
            [{"index": 3, "name": "Other mic", "hostapi": "Windows WASAPI"}],
            [{"index": i, "name": "USB mic", "hostapi": "Windows WASAPI"} for i in (3, 15)],
        ):
            with self.subTest(devices=devices), \
                 patch("core.audio_devices.resolve_input_device", return_value=None), \
                 patch("core.audio_devices.sd.query_devices", return_value={"name": "USB mic"}), \
                 patch("core.audio_devices.input_devices", return_value=devices):
                self.assertIsNone(resolve_capture_device(None))

    def test_explicit_endpoint_is_respected(self):
        with patch("core.audio_devices.resolve_input_device", return_value=1), \
             patch("core.audio_devices.input_devices") as devices:
            self.assertEqual(resolve_capture_device({"index": 1}), 1)
            devices.assert_not_called()


class AudioIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def run_recording(self, blocks, save=False):
        app = SimpleNamespace(state=ClientState())
        recorder = AudioRecorder(app)
        recorder._send_message = AsyncMock()
        app.state.queue_in.put_nowait({"type": "begin", "time": 1000, "data": None})
        for timestamp, samples in blocks:
            app.state.queue_in.put_nowait({"type": "data", "time": timestamp, "data": samples})
        app.state.queue_in.put_nowait({"type": "finish", "time": 1001, "data": None})
        with patch.object(ClientConfig, "save_audio", save), \
             patch.object(ClientConfig, "threshold", .3):
            await recorder.record_and_send()
            await asyncio.sleep(0)
        messages = [call.args[0] for call in recorder._send_message.await_args_list]
        return recorder, messages

    async def test_threshold_crossing_does_not_drop_current_frame(self):
        before = np.full((2400, 1), .25, dtype=np.float32)
        after = np.full((2400, 1), .75, dtype=np.float32)
        recorder, messages = await self.run_recording([(1000.1, before), (1000.4, after)])
        self.assertEqual([m.is_final for m in messages], [False, True])
        samples = np.frombuffer(base64.b64decode(messages[0].data), dtype=np.float32)
        np.testing.assert_array_equal(samples, np.concatenate([before[::3, 0], after[::3, 0]]))
        self.assertAlmostEqual(recorder._duration, .1)

    async def test_exact_zero_audio_never_reaches_recognizer(self):
        _, messages = await self.run_recording([
            (1000.1, np.zeros((2400, 1), dtype=np.float32)),
            (1000.5, np.zeros((2400, 1), dtype=np.float32)),
        ])
        self.assertEqual(messages, [])

    async def test_no_frames_never_reaches_recognizer(self):
        _, messages = await self.run_recording([])
        self.assertEqual(messages, [])

    async def test_quiet_nonzero_audio_is_not_mistaken_for_silence(self):
        _, messages = await self.run_recording([
            (1000.5, np.full((960, 1), 1e-6, dtype=np.float32)),
        ])
        self.assertEqual([m.is_final for m in messages], [False, True])

    async def test_cached_short_audio_is_saved_before_finalizing(self):
        with patch("core.client.audio.recorder.AudioFileManager") as manager:
            manager.return_value.create.return_value = (Path("unused.wav"), Mock())
            _, messages = await self.run_recording([
                (1000.1, np.full((960, 1), .1, dtype=np.float32)),
            ], save=True)
            manager.return_value.create.assert_called_once_with(1, 1000)
            manager.return_value.write.assert_called_once()
            self.assertTrue(manager.return_value.finish.called)
        self.assertEqual([m.is_final for m in messages], [False, True])

    async def test_silence_marker_cannot_trigger_typing_llm_or_hotwords(self):
        for text in ("/sil", " /SIL ", "", "  "):
            with self.subTest(text=text):
                app = SimpleNamespace(state=ClientState(), hotword=Mock(),
                                      output=Mock(), llm=Mock())
                app.state.register_audio_file("test", Path("retained.wav"))
                message = SimpleNamespace(text=text, task_id="test", is_final=True,
                                          time_complete=2, time_submit=1)
                await ResultProcessor(app)._handle_message(message)
                self.assertNotIn("test", app.state.audio_files)
                app.hotword.get_phoneme_corrector.assert_not_called()
                app.output.output.assert_not_called()
                app.llm.process_and_output.assert_not_called()
