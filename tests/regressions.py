import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from core.server.engines.onnx_session import OnnxSession
from core.server.engines.fun_asr_gguf.inference.encoder import AudioEncoder
from core.server.engines.fun_asr_gguf.inference.ctc_decoder import CTCDecoder
from core.client.shortcut.event_handler import ShortcutEventHandler
from core.audio_devices import input_devices, physical_input_devices, resolve_input_device
from core.client.audio.stream import AudioStreamManager
from config_client import ClientConfig
from core.server.engines.qwen_asr_gguf.inference.encoder import encoder_provider


class RegressionTests(unittest.TestCase):
    def test_local_decode_failure_is_reported_without_typing_payload(self):
        import queue
        from config_server import ServerConfig
        from core.server.engines.errors import RecognitionFailure
        from core.server.state import WorkerState
        from core.server.worker.task_handler import TaskHandler
        handler = TaskHandler(queue.Queue(), queue.Queue(), [], WorkerState())
        handler.pipeline = Mock()
        handler.pipeline.process.side_effect = RecognitionFailure("Qwen decode failed")
        task = SimpleNamespace(task_id="failure", socket_id="socket", type="mic", is_final=True)
        with patch.object(ServerConfig, "model_type", "qwen_asr"):
            handler.handle_audio_task(task)
        result = handler.queue_out.get_nowait()
        self.assertEqual(result.text, "")
        self.assertEqual(result.error, "Qwen decode failed")
        self.assertTrue(result.is_final)
        self.assertNotIn(task.task_id, handler.state.sessions)

    def test_downloaded_qwen_without_manifest_uses_cpu_encoder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("qwen3_asr_encoder_frontend.onnx", "qwen3_asr_encoder_backend.onnx"):
                self.assertEqual(encoder_provider(root / name, "DML"), "CPU")
                self.assertEqual(encoder_provider(root / name, "CPU"), "CPU")
                self.assertEqual(encoder_provider(root / name, "CUDA"), "CUDA")

    def test_qwen_decode_failure_is_not_returned_as_text(self):
        from core.server.engines.qwen_asr_gguf.inference.asr import QwenASREngine
        from core.server.engines.errors import RecognitionFailure
        engine = object.__new__(QwenASREngine)
        engine._decode = Mock(return_value=SimpleNamespace(is_aborted=True, text="garbage"))
        with self.assertRaises(RecognitionFailure):
            engine._safe_decode(np.zeros((1, 1)), "", 5, True, 0.4, streaming=False)
        self.assertEqual(engine._decode.call_count, 4)

    def test_qwen_decode_retry_can_recover(self):
        from core.server.engines.qwen_asr_gguf.inference.asr import QwenASREngine
        engine = object.__new__(QwenASREngine)
        success = SimpleNamespace(is_aborted=False, text="valid")
        engine._decode = Mock(side_effect=[SimpleNamespace(is_aborted=True), success])
        self.assertIs(engine._safe_decode(np.zeros((1, 1)), "", 5, True, 0.4,
                                         streaming=False), success)

    def test_renamed_qwen_int4_avoids_incorrect_directml_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model-source.json").write_text(json.dumps({"files": [{
                "source": "backend.int4.onnx", "target": "backend.onnx"}]}))
            self.assertEqual(encoder_provider(root / "backend.onnx", "DML"), "CPU")
            self.assertEqual(encoder_provider(root / "backend.onnx", "CUDA"), "CUDA")
            self.assertEqual(encoder_provider(root / "other.onnx", "DML"), "DML")

    def test_input_devices_excludes_outputs(self):
        with patch("core.audio_devices.sd.query_hostapis", return_value=[{"name": "WASAPI"}]), \
             patch("core.audio_devices.sd.query_devices", return_value=[
                 {"name": "Speaker", "hostapi": 0, "max_input_channels": 0},
                 {"name": "USB Mic", "hostapi": 0, "max_input_channels": 2}]):
            self.assertEqual(input_devices(), [
                {"index": 1, "name": "USB Mic", "hostapi": "WASAPI", "channels": 2}])

    def test_microphone_selection_survives_reordering(self):
        saved = {"index": 1, "name": "USB Mic", "hostapi": "WASAPI"}
        devices = [{"index": 1, "name": "Other", "hostapi": "WASAPI"},
                   {"index": 9, "name": "USB Mic", "hostapi": "WASAPI"}]
        self.assertEqual(resolve_input_device(saved, devices), 9)
        self.assertIsNone(resolve_input_device(None, devices))

    def test_physical_devices_prefer_wasapi_endpoint(self):
        devices = [
            {"index": 0, "name": "USB Mic", "hostapi": "MME", "channels": 1},
            {"index": 7, "name": "USB Mic", "hostapi": "Windows WASAPI", "channels": 2},
            {"index": 8, "name": "Other Mic", "hostapi": "Windows DirectSound", "channels": 1},
        ]
        result = physical_input_devices(devices)
        self.assertEqual([d["name"] for d in result], ["USB Mic", "Other Mic"])
        self.assertEqual(result[0]["index"], 7)
        self.assertEqual(result[0]["hostapi"], "Windows WASAPI")

    def test_missing_microphone_does_not_silently_switch(self):
        with self.assertRaises(ValueError):
            resolve_input_device({"name": "Missing", "hostapi": "WASAPI"}, [])

    def test_selected_microphone_is_used_by_stream(self):
        app = SimpleNamespace(state=SimpleNamespace(stream=None))
        manager = AudioStreamManager(app)
        manager.keep_open = False
        self.addCleanup(manager.shutdown)
        selected = {"index": 15, "name": "USB Mic", "hostapi": "WASAPI"}
        with patch.object(ClientConfig, "audio_device", selected), \
             patch("core.client.audio.stream.resolve_capture_device", return_value=15) as resolve, \
             patch("core.client.audio.stream.sd.query_devices",
                   return_value={"max_input_channels": 2, "name": "USB Mic"}) as query, \
             patch("core.client.audio.stream.sd.InputStream") as stream:
            self.assertIs(manager.start(), stream.return_value)
            resolve.assert_called_once_with(selected)
            query.assert_called_once_with(15, kind="input")
            self.assertEqual(stream.call_args.kwargs["device"], 15)
            stream.return_value.start.assert_called_once()
            manager.stop()
            stream.return_value.close.assert_called_once()

    def test_gpu_runtime_failure_retries_once(self):
        session = OnnxSession.__new__(OnnxSession)
        session.model_path = "test.onnx"
        session._gpu_active = True
        session._session = Mock()
        session._session.run.side_effect = RuntimeError("GPU unavailable")
        cpu = Mock()
        cpu.run.return_value = ["result"]
        with patch.object(session, "_create", return_value=cpu) as create:
            self.assertEqual(session.run(None, {"audio": 1}), ["result"])
            create.assert_called_once_with(["CPUExecutionProvider"])
            self.assertFalse(session._gpu_active)
            cpu.run.side_effect = ValueError("bad input")
            with self.assertRaises(ValueError):
                session.run(None, {})
            create.assert_called_once()

    def test_raw_waveform_schema(self):
        encoder = AudioEncoder.__new__(AudioEncoder)
        encoder._raw_audio_input = True
        encoder.input_dtype = np.float32
        encoder.sess = Mock()
        enc = np.zeros((1, 17, 512), dtype=np.float32)
        adapt = np.ones((1, 3, 1024), dtype=np.float32)
        encoder.sess.run.return_value = [enc, adapt]
        embeddings, output = encoder.encode(np.zeros(16000))
        self.assertEqual(embeddings.shape, (3, 1024))
        self.assertIs(output, enc)
        self.assertEqual(encoder.sess.run.call_args.args[1]["audio"].shape, (1, 1, 16000))

    def test_ctc_legacy_logits_and_topk(self):
        ctc = CTCDecoder.__new__(CTCDecoder)
        ctc.input_dtype = np.float32
        ctc.sess = Mock()
        ctc.sess.run.return_value = [np.array([[[1000., 1002., 1001.]]])]
        scores, ids = ctc._infer(np.zeros((1, 1, 512)))
        self.assertEqual(ids.tolist(), [[[1, 2, 0]]])
        self.assertTrue(np.isfinite(scores).all())
        self.assertAlmostEqual(float(np.exp(scores).sum()), 1., places=5)
        ctc.sess.run.return_value = [scores, ids]
        actual = ctc._infer(np.zeros((1, 1, 512)))
        self.assertIs(actual[0], scores)
        self.assertIs(actual[1], ids)

    def test_capslock_hold_release_and_repeat(self):
        task = Mock()
        task.shortcut = SimpleNamespace(hold_mode=True, suppress=True)
        task.pressed = False
        task.is_recording = False
        task.threshold = .3
        handler = ShortcutEventHandler({}, Mock(), Mock())
        handler.handle_keydown("caps_lock", task)
        task.launch.assert_called_once()
        task.is_recording = True
        handler.handle_keydown("caps_lock", task)
        task.launch.assert_called_once()
        task.recording_start_time = 10
        with patch("core.client.shortcut.event_handler.time.time", return_value=11):
            handler.handle_keyup("caps_lock", task)
        task.finish.assert_called_once()

    def test_capslock_failure_does_not_retry_until_physical_release(self):
        task = Mock()
        task.shortcut = SimpleNamespace(hold_mode=True)
        task.pressed = False
        task.is_recording = False
        handler = ShortcutEventHandler({}, Mock(), Mock())
        for _ in range(10):
            handler.handle_keydown("caps_lock", task)
        task.launch.assert_called_once()
        handler.handle_keyup("caps_lock", task)
        self.assertFalse(task.pressed)
        handler.handle_keydown("caps_lock", task)
        self.assertEqual(task.launch.call_count, 2)

    def test_mouse_hold_can_retry_after_failed_open_and_release(self):
        from core.client.shortcut.shortcut_manager import ShortcutManager
        task = Mock()
        task.shortcut = SimpleNamespace(hold_mode=True)
        task.pressed = False
        task.is_recording = False
        handler = ShortcutEventHandler({}, Mock(), Mock())
        handler.handle_keydown("x2", task)
        self.assertTrue(task.pressed)
        ShortcutManager._handle_mouse_keyup(Mock(), "x2", task)
        self.assertFalse(task.pressed)
        handler.handle_keydown("x2", task)
        self.assertEqual(task.launch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
