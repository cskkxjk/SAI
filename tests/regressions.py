import unittest
import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
import tarfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

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

    def test_physical_devices_drop_non_wasapi_duplicates(self):
        devices = [
            {"index": 0, "name": "USB Mic", "hostapi": "MME", "channels": 1},
            {"index": 7, "name": "USB Mic", "hostapi": "Windows WASAPI", "channels": 2},
            {"index": 8, "name": "Other Mic", "hostapi": "Windows DirectSound", "channels": 1},
        ]
        result = physical_input_devices(devices)
        self.assertEqual([d["name"] for d in result], ["USB Mic"])
        self.assertEqual(result[0]["index"], 7)
        self.assertEqual(result[0]["hostapi"], "Windows WASAPI")

    def test_physical_devices_only_show_wasapi_endpoints_when_available(self):
        devices = [
            {"index": 0, "name": "Microsoft Sound Mapper - Input", "hostapi": "MME", "channels": 2},
            {"index": 1, "name": "Mic", "hostapi": "MME", "channels": 2},
            {"index": 7, "name": "Mic", "hostapi": "Windows DirectSound", "channels": 2},
            {"index": 17, "name": "Mic", "hostapi": "Windows WASAPI", "channels": 2},
            {"index": 18, "name": "Headset", "hostapi": "Windows WASAPI", "channels": 1},
        ]
        result = physical_input_devices(devices)
        self.assertEqual([d["name"] for d in result], ["Headset", "Mic"])
        self.assertEqual({d["hostapi"] for d in result}, {"Windows WASAPI"})

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
            query.assert_any_call(15, kind="input")
            self.assertEqual(stream.call_args.kwargs["device"], 15)
            stream.return_value.start.assert_called_once()
            manager.stop()
            stream.return_value.close.assert_called_once()

    def test_stream_falls_back_to_another_host_api_of_the_same_mic(self):
        from sounddevice import PortAudioError

        app = SimpleNamespace(state=SimpleNamespace(stream=None))
        manager = AudioStreamManager(app)
        manager.keep_open = False
        self.addCleanup(manager.shutdown)
        selected = {"index": 15, "name": "USB Mic", "hostapi": "WASAPI"}
        devices = [
            {"index": 15, "name": "USB Mic", "hostapi": "Windows WASAPI",
             "max_input_channels": 2, "default_samplerate": 48000},
            {"index": 3, "name": "USB Mic", "hostapi": "Windows DirectSound",
             "max_input_channels": 2, "default_samplerate": 48000},
        ]
        handles = []

        def open_stream(samplerate, blocksize, device, **kwargs):
            if device == 15:
                raise PortAudioError("Error opening InputStream: Invalid sample rate")
            handle = Mock()
            handles.append(handle)
            return handle

        def query(index=None, kind=None):
            return next((item for item in devices if item["index"] == index),
                        devices[0])

        with patch.object(ClientConfig, "audio_device", selected), \
             patch("core.client.audio.stream.input_devices", return_value=devices), \
             patch("core.client.audio.stream.resolve_capture_device", return_value=15), \
             patch("core.client.audio.stream.sd.query_devices", side_effect=query), \
             patch("core.client.audio.stream.sd.InputStream", side_effect=open_stream):
            opened = manager.start()
            self.assertEqual(len(handles), 1)
            self.assertIs(opened, handles[0])
            opened.start.assert_called_once()
            manager.stop()
            opened.close.assert_called_once()

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

    def test_gpu_fallback_explains_undecodable_driver_messages(self):
        failure = UnicodeDecodeError("utf-8", b"\xd3", 0, 1, "invalid continuation byte")
        self.assertIn("无法解码", OnnxSession._describe(failure))
        self.assertEqual(OnnxSession._describe(ValueError("普通错误")), "普通错误")

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

    def test_recording_state_toggles_the_tray_icon(self):
        from core.client.state import ClientState
        app = SimpleNamespace(tray=Mock())
        state = ClientState(app=app)
        state.start_recording(1.0)
        state.stop_recording()
        self.assertEqual(
            [call.args[0] for call in app.tray.set_recording.call_args_list],
            [True, False])

    def test_recording_flag_file_tracks_the_recording_state(self):
        from core.client.state import ClientState
        with tempfile.TemporaryDirectory() as folder:
            flag = Path(folder) / ".recording-client"
            with patch.dict("os.environ", {"SAI_RECORDING_FLAG": str(flag)}):
                state = ClientState(app=SimpleNamespace(tray=None))
                state.start_recording(1.0)
                self.assertTrue(flag.exists())
                state.stop_recording()
                self.assertFalse(flag.exists())

    def test_launcher_tray_indicator_follows_the_flag_file(self):
        import gui_launcher
        with tempfile.TemporaryDirectory() as folder:
            flag = Path(folder) / ".recording-client"
            launcher = gui_launcher.Launcher.__new__(gui_launcher.Launcher)
            launcher.tray_icon = SimpleNamespace(icon="blue", title="tray")
            launcher.tray_images = {False: "blue", True: "red"}
            launcher.tray_recording = False
            launcher.phrase_recording = False
            launcher.recording_files = {"client": flag}
            launcher._refresh_recording_indicator()
            self.assertEqual(launcher.tray_icon.icon, "blue")
            flag.write_text("1", encoding="utf-8")
            launcher._refresh_recording_indicator()
            self.assertEqual(launcher.tray_icon.icon, "red")
            self.assertIn("录音", launcher.tray_icon.title)
            flag.unlink()
            launcher._refresh_recording_indicator()
            self.assertEqual(launcher.tray_icon.icon, "blue")
            self.assertEqual(launcher.tray_icon.title, gui_launcher.APP_TITLE)

    def test_phrase_recording_switches_the_tray_icon(self):
        """录制语音短语时托盘图标与听写一样变红。"""
        import gui_launcher
        launcher = gui_launcher.Launcher.__new__(gui_launcher.Launcher)
        launcher.tray_icon = SimpleNamespace(icon="blue", title="tray")
        launcher.tray_images = {False: "blue", True: "red"}
        launcher.tray_recording = False
        launcher.recording_files = {}
        launcher.phrase_recording = False
        launcher.set_phrase_recording(True)
        self.assertEqual(launcher.tray_icon.icon, "red")
        self.assertIn("录音", launcher.tray_icon.title)
        launcher.set_phrase_recording(False)
        self.assertEqual(launcher.tray_icon.icon, "blue")
        self.assertEqual(launcher.tray_icon.title, gui_launcher.APP_TITLE)

    def test_tray_icon_switches_between_idle_and_recording(self):
        from core.ui import tray
        system = tray._TraySystem.__new__(tray._TraySystem)
        system.title = "SAI Client"
        system.recording = False
        system.images = {False: "blue", True: "red"}
        system.icon = SimpleNamespace(icon="blue", title="SAI Client")
        system.set_recording(True)
        self.assertEqual(system.icon.icon, "red")
        self.assertIn("录音", system.icon.title)
        system.set_recording(True)
        self.assertEqual(system.icon.icon, "red")
        system.set_recording(False)
        self.assertEqual(system.icon.icon, "blue")
        self.assertEqual(system.icon.title, "SAI Client")


    def test_dropped_media_files_start_file_transcription(self):
        import sai
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "meeting.mp4"
            audio.write_bytes(b"media")
            self.assertEqual(sai.transcription_request([str(audio)]), [str(audio)])
            self.assertEqual(sai.dropped_files(["--client", str(audio), folder]), [str(audio)])
            self.assertEqual(sai.transcription_request(["--self-test", str(audio)]), [])
            self.assertEqual(sai.transcription_request(["--server", "--client"]), [])
            self.assertEqual(sai.transcription_request([str(Path(folder) / "missing.wav")]), [])


    def test_paste_text_waits_for_the_clipboard_before_and_after_the_paste(self):
        import asyncio
        from unittest.mock import MagicMock
        from core.client.clipboard import clipboard

        written = []
        state = {"value": "用户原来的剪贴板内容"}
        controller = MagicMock()
        sleeps = []

        def fake_copy(value):
            written.append(value)
            state["value"] = value

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        with patch.object(clipboard.pyclip, "copy", fake_copy), \
                patch.object(clipboard.pyclip, "paste",
                             lambda: state["value"].encode("utf-8")), \
                patch.object(clipboard.keyboard, "Controller",
                             return_value=controller), \
                patch.object(clipboard.asyncio, "sleep", fake_sleep), \
                patch.object(clipboard, "_target_delay_factor", lambda: 1.0), \
                patch.object(ClientConfig, "paste_settle_delay", 0.12), \
                patch.object(ClientConfig, "restore_clip_delay", 0.5):
            asyncio.run(clipboard.paste_text("识别结果", restore_clipboard=True))

        self.assertEqual(written, ["识别结果", "用户原来的剪贴板内容"])
        self.assertEqual(sleeps, [0.12, 0.5])
        controller.tap.assert_called_once_with("v")

    def test_paste_text_scales_delays_for_remote_desktop_targets(self):
        import asyncio
        from unittest.mock import MagicMock
        from core.client.clipboard import clipboard

        for process_name in ("mstsc.exe", "rvlsession.exe"):
            state = {"value": "旧内容"}
            controller = MagicMock()
            sleeps = []

            async def fake_sleep(seconds, _sleeps=sleeps):
                _sleeps.append(seconds)

            with patch.object(clipboard.pyclip, "copy",
                              lambda value: state.update(value=value)), \
                    patch.object(clipboard.pyclip, "paste",
                                 lambda: state["value"].encode("utf-8")), \
                    patch.object(clipboard.keyboard, "Controller",
                                 return_value=controller), \
                    patch.object(clipboard.asyncio, "sleep", fake_sleep), \
                    patch("core.tools.window_detector.get_active_window_info",
                          return_value={"process_name": process_name}), \
                    patch.object(ClientConfig, "paste_settle_delay", 0.2), \
                    patch.object(ClientConfig, "restore_clip_delay", 0.5):
                asyncio.run(clipboard.paste_text("识别结果", restore_clipboard=True))

            self.assertAlmostEqual(sleeps[0], clipboard.SLOW_TARGET_SETTLE_DELAY,
                                   msg=process_name)
            self.assertAlmostEqual(sleeps[1], clipboard.SLOW_TARGET_RESTORE_DELAY,
                                   msg=process_name)
            self.assertEqual(state["value"], "旧内容", msg=process_name)

    def test_paste_text_keeps_a_newer_clipboard_copy(self):
        import asyncio
        from unittest.mock import MagicMock
        from core.client.clipboard import clipboard

        written = []
        state = {"value": "用户原来的剪贴板内容"}
        controller = MagicMock()

        def fake_copy(value):
            written.append(value)
            state["value"] = value

        async def fake_sleep(seconds):
            state["value"] = "用户新复制的内容"

        with patch.object(clipboard.pyclip, "copy", fake_copy), \
                patch.object(clipboard.pyclip, "paste",
                             lambda: state["value"].encode("utf-8")), \
                patch.object(clipboard.keyboard, "Controller",
                             return_value=controller), \
                patch.object(clipboard.asyncio, "sleep", fake_sleep), \
                patch.object(clipboard, "_target_delay_factor", lambda: 1.0), \
                patch.object(ClientConfig, "paste_settle_delay", 0.12), \
                patch.object(ClientConfig, "restore_clip_delay", 0.5):
            asyncio.run(clipboard.paste_text("识别结果", restore_clipboard=True))

        self.assertEqual(written, ["识别结果"])
        self.assertEqual(state["value"], "用户新复制的内容")

    def test_remote_target_needs_paste_only_for_non_ascii_text(self):
        from core.client.clipboard import needs_paste_for_remote

        with patch("core.tools.window_detector.get_active_window_info",
                   return_value={"process_name": "mstsc.exe"}):
            self.assertTrue(needs_paste_for_remote("你好，世界"))
            self.assertFalse(needs_paste_for_remote("hello world"))
        with patch("core.tools.window_detector.get_active_window_info",
                   return_value={"process_name": "notepad.exe"}):
            self.assertFalse(needs_paste_for_remote("你好"))

    def test_typing_into_a_remote_target_uses_pynput(self):
        from core.client.output.text_output import TextOutput

        with patch("core.client.clipboard.is_remote_target", return_value=True), \
                patch("core.client.output.text_output.PynputController") as controller, \
                patch("core.client.output.text_output.key_send.write") as write:
            TextOutput()._type_text("ab")
            controller.assert_called_once()
            self.assertEqual([call.args for call in controller.return_value.type.call_args_list],
                             [("a",), ("b",)])
            write.assert_not_called()

        with patch("core.client.clipboard.is_remote_target", return_value=False), \
                patch("core.client.output.text_output.PynputController") as controller, \
                patch("core.client.output.text_output.key_send.write") as write:
            TextOutput()._type_text("hello")
            write.assert_called_once_with("hello")
            controller.assert_not_called()

    def test_output_falls_back_to_paste_for_remote_chinese(self):
        import asyncio
        from core.client.output.text_output import TextOutput

        async def fake_paste(*args, **kwargs):
            return None

        with patch("core.client.clipboard.needs_paste_for_remote", return_value=True), \
                patch("core.client.clipboard.paste_text", side_effect=fake_paste) as paste, \
                patch("core.client.output.text_output.type_text") as type_text:
            asyncio.run(TextOutput().output("你好，世界", paste=False))

        paste.assert_called_once()
        self.assertEqual(paste.call_args.args, ("你好，世界",))
        type_text.assert_not_called()

    def test_output_types_locally_through_a_worker_thread(self):
        import asyncio
        from core.client.output.text_output import TextOutput

        with patch("core.client.clipboard.needs_paste_for_remote", return_value=False), \
                patch("core.client.output.text_output.type_text") as type_text:
            asyncio.run(TextOutput().output("hello", paste=False))

        type_text.assert_called_once_with("hello")

    def test_stuck_modifiers_are_released_on_startup(self):
        from core.tools import stuck_keys

        class FakeUser32:
            def __init__(self):
                self.lifted = []

            def GetAsyncKeyState(self, vk):
                return 0x8000 if vk == 0xA2 else 0

            def keybd_event(self, vk, scan, flags, extra):
                self.lifted.append((vk, flags))

        fake = FakeUser32()
        with patch.object(stuck_keys.ctypes, "windll", SimpleNamespace(user32=fake)):
            released = stuck_keys.release_stuck_modifiers()

        self.assertEqual(released, ["左 Ctrl"])
        self.assertEqual(fake.lifted, [(0xA2, stuck_keys.KEYEVENTF_KEYUP)])

    def test_paste_text_retries_when_the_clipboard_write_does_not_stick(self):
        import asyncio
        from unittest.mock import MagicMock
        from core.client.clipboard import clipboard

        attempts = []
        state = {"value": "旧内容"}
        controller = MagicMock()

        def flaky_copy(value):
            attempts.append(value)
            if len(attempts) > 1:
                state["value"] = value

        async def fake_sleep(seconds):
            pass

        with patch.object(clipboard.pyclip, "copy", flaky_copy), \
                patch.object(clipboard.pyclip, "paste",
                             lambda: state["value"].encode("utf-8")), \
                patch.object(clipboard.keyboard, "Controller",
                             return_value=controller), \
                patch.object(clipboard.asyncio, "sleep", fake_sleep), \
                patch.object(clipboard, "_target_delay_factor", lambda: 1.0), \
                patch.object(ClientConfig, "restore_clip_delay", 0.5):
            asyncio.run(clipboard.paste_text("识别结果", restore_clipboard=True))

        self.assertEqual(attempts, ["识别结果", "识别结果", "旧内容"])
        controller.tap.assert_called_once_with("v")


    def test_shortcut_dataclass_accepts_the_paste_flag(self):
        from core.client.shortcut.shortcut_config import Shortcut
        shortcut = Shortcut(**{
            "key": "x1", "type": "mouse", "suppress": True,
            "hold_mode": True, "enabled": True, "paste": True,
        })
        self.assertTrue(shortcut.paste)
        self.assertFalse(Shortcut(key="caps_lock").paste)

    def test_paste_override_survives_until_it_is_consumed(self):
        from core.client.state import ClientState
        state = ClientState()
        self.assertIsNone(state.consume_paste_override())
        state.set_paste_override(True)
        self.assertTrue(state.consume_paste_override())
        self.assertIsNone(state.consume_paste_override())
        state.set_paste_override(True)
        state.reset()
        self.assertIsNone(state.consume_paste_override())

    def test_paste_shortcut_marks_only_its_own_recording(self):
        import asyncio
        import threading
        from core.client.shortcut.shortcut_config import Shortcut
        from core.client.shortcut.task import ShortcutTask
        from core.client.state import ClientState

        async def idle_session(self):
            return None

        def fake_submit(coro, loop):
            coro.close()
            return Mock()

        for paste, expected in ((True, True), (False, None)):
            state = ClientState()
            app = SimpleNamespace(
                state=state,
                loop=Mock(),
                stream=SimpleNamespace(session_lock=threading.Lock()),
            )
            task = ShortcutTask(app, Shortcut(key="x1", type="mouse", paste=paste))
            with patch.object(ShortcutTask, "_run_session", idle_session), \
                    patch.object(asyncio, "run_coroutine_threadsafe", fake_submit):
                task.launch()
            self.assertEqual(state.paste_override, expected)
            task.finish()
            # 标记保留到结果输出时被取走消费
            self.assertEqual(state.paste_override, expected)

    def test_launcher_saves_two_shortcuts_with_distinct_roles(self):
        import gui_launcher
        launcher = gui_launcher.Launcher.__new__(gui_launcher.Launcher)
        launcher.shortcut_capture = SimpleNamespace(
            value={"key": "caps_lock", "type": "keyboard"})
        launcher.paste_capture = SimpleNamespace(
            value={"key": "x1", "type": "mouse"})

        data = launcher._shortcut_data()

        self.assertEqual([item["key"] for item in data], ["caps_lock", "x1"])
        self.assertTrue(data[1]["paste"])
        self.assertTrue(all(item["suppress"] and item["hold_mode"] for item in data))
        self.assertNotIn("paste", data[0])

        launcher.paste_capture = SimpleNamespace(
            value={"key": "caps_lock", "type": "keyboard"})
        self.assertEqual(len(launcher._shortcut_data()), 1)

        launcher.paste_capture = SimpleNamespace(value={"key": "", "type": "keyboard"})
        launcher.shortcut_capture = SimpleNamespace(value={"key": "", "type": "keyboard"})
        self.assertEqual(launcher._shortcut_data(), [])


class ShortcutHotReloadTests(unittest.TestCase):
    """快捷键配置热重载（免重启）"""

    @staticmethod
    def _manager(shortcuts):
        from core.client.shortcut.shortcut_manager import ShortcutManager
        from core.client.state import ClientState
        app = SimpleNamespace(state=ClientState())
        return ShortcutManager(app, shortcuts)

    def test_load_shortcuts_reads_the_gui_config(self):
        from core.client.shortcut.shortcut_config import load_shortcuts
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config_gui.json"
            path.write_text(json.dumps({"shortcuts": [
                {"key": "caps_lock", "type": "keyboard", "unknown": 1},
                {"key": "x1", "type": "mouse", "paste": True},
                {"key": "", "type": "keyboard"},
            ]}), encoding="utf-8")
            shortcuts = load_shortcuts(path)
            self.assertEqual([item.key for item in shortcuts], ["caps_lock", "x1"])
            self.assertTrue(shortcuts[1].paste)
            self.assertFalse(load_shortcuts(Path(folder) / "missing.json"))

    def test_reload_rebuilds_tasks_in_place(self):
        from core.client.shortcut.shortcut_config import Shortcut
        from core.client.shortcut.shortcut_manager import ShortcutManager
        manager = self._manager([Shortcut(key="caps_lock")])
        with patch.object(ShortcutManager, "start", lambda self: None):
            manager.reload([Shortcut(key="x1", type="mouse", paste=True)])
        self.assertEqual(set(manager.tasks), {"x1"})
        self.assertTrue(manager.tasks["x1"].shortcut.paste)

    def test_config_watcher_only_reloads_when_the_file_changes(self):
        from core.client.shortcut import shortcut_manager as module
        from core.client.shortcut.shortcut_config import Shortcut
        manager = self._manager([Shortcut(key="caps_lock")])
        fresh = [Shortcut(key="f9")]
        with patch.object(module, "load_shortcuts", lambda path=None: fresh), \
                patch.object(module.ShortcutManager, "start", lambda self: None):
            self.assertTrue(manager._poll_config())
            self.assertEqual(set(manager.tasks), {"f9"})
            self.assertFalse(manager._poll_config())

    def test_config_watcher_thread_starts_and_stops(self):
        from core.client.shortcut import shortcut_manager as module
        from core.client.shortcut.shortcut_config import Shortcut
        manager = self._manager([Shortcut(key="caps_lock")])
        with patch.object(module, "load_shortcuts", lambda path=None: []):
            manager.start_config_watcher()
            self.assertTrue(manager._config_thread.is_alive())
            manager.stop_config_watcher()
        self.assertIsNone(manager._config_thread)

    def test_capture_flag_pauses_shortcut_triggers(self):
        """语音短语录制标记：存在时暂停触发，启动器退出后自动清理。"""
        from core.client.shortcut.shortcut_config import Shortcut
        manager = self._manager([Shortcut(key="caps_lock")])
        with tempfile.TemporaryDirectory() as folder:
            flag = Path(folder) / "voice-capture.flag"
            manager._capture_flag = flag
            self.assertFalse(manager._poll_capture_hold())
            flag.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            self.assertTrue(manager._poll_capture_hold())
            self.assertTrue(manager.capture_hold)
            flag.write_text(json.dumps({"pid": 999999999}), encoding="utf-8")
            self.assertFalse(manager._poll_capture_hold())
            self.assertFalse(manager.capture_hold)
            self.assertFalse(flag.exists())

    def test_capture_flag_file_is_checked_before_the_poll_interval(self):
        """按键命中快捷键时直接查标记文件，消除轮询间隙。"""
        from core.client.shortcut.shortcut_config import Shortcut
        manager = self._manager([Shortcut(key="caps_lock")])
        with tempfile.TemporaryDirectory() as folder:
            flag = Path(folder) / "voice-capture.flag"
            manager._capture_flag = flag
            self.assertFalse(manager._capture_hold_active())
            flag.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            self.assertTrue(manager._capture_hold_active())
            flag.write_text(json.dumps({"pid": 999999999}), encoding="utf-8")
            self.assertFalse(manager._capture_hold_active())

    def test_capture_hold_releases_and_restores_the_microphone(self):
        """录制语音短语期间客户端让出常开的麦克风。"""
        from core.client.shortcut.shortcut_config import Shortcut
        from core.client.shortcut.shortcut_manager import ShortcutManager
        from core.client.state import ClientState
        stream = Mock()
        app = SimpleNamespace(state=ClientState(), stream=stream)
        manager = ShortcutManager(app, [Shortcut(key="caps_lock")])
        manager._apply_capture_hold(True).join(timeout=5)
        stream.suspend_for_capture.assert_called_once()
        manager._apply_capture_hold(False).join(timeout=5)
        stream.resume_after_capture.assert_called_once()
        # 重复状态不再重复调整
        manager._apply_capture_hold(False).join(timeout=5)
        stream.resume_after_capture.assert_called_once()

    def test_keep_open_microphone_is_released_for_capture(self):
        """常开麦克风只在空闲时让出，正在录音时不动。"""
        from core.client.audio.stream import AudioStreamManager
        opened = Mock()
        state = SimpleNamespace(stream=opened, recording=False)
        manager = AudioStreamManager(SimpleNamespace(state=state))
        self.addCleanup(manager.shutdown)
        manager.keep_open = True
        manager._suspend_for_capture()
        opened.close.assert_called_once()
        self.assertIsNone(state.stream)

        state.stream = Mock()
        manager.keep_open = False
        manager._suspend_for_capture()
        state.stream.close.assert_not_called()

        manager.keep_open = True
        state.recording = True
        manager._suspend_for_capture()
        state.stream.close.assert_not_called()

    def test_capture_hold_ignores_new_triggers(self):
        from core.client.shortcut.shortcut_manager import (
            ShortcutManager, WM_KEYDOWN, WM_KEYUP)
        from core.client.shortcut.shortcut_config import Shortcut

        class Recorder:
            def __init__(self):
                self.events = []

            def handle_keydown(self, key_name, task):
                task.pressed = True
                self.events.append(("down", key_name))

            def handle_keyup(self, key_name, task):
                task.pressed = False
                self.events.append(("up", key_name))

        manager = ShortcutHotReloadTests._manager([])
        manager.tasks.clear()
        manager.tasks["caps_lock"] = SimpleNamespace(
            shortcut=Shortcut(key="caps_lock"), pressed=False, released=True)
        manager._pressed_keys = set()
        manager._capture_flag = None
        manager.keyboard_listener = None
        manager._event_handler = Recorder()
        event_filter = manager.create_keyboard_filter()

        manager.capture_hold = True
        event_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0x14))
        event_filter(WM_KEYUP, SimpleNamespace(vkCode=0x14))
        self.assertEqual(manager._event_handler.events, [])

        manager.capture_hold = False
        event_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0x14))
        self.assertEqual(manager._event_handler.events, [("down", "caps_lock")])


class DarwinSuppressTests(unittest.TestCase):
    """macOS：录音键通过 darwin_intercept 系统级屏蔽（Windows 上模拟回调）。"""

    @staticmethod
    def _manager(shortcuts):
        from core.client.shortcut.shortcut_manager import ShortcutManager
        from core.client.state import ClientState
        app = SimpleNamespace(state=ClientState())
        manager = ShortcutManager(app, shortcuts)
        manager._capture_flag = None
        return manager

    @staticmethod
    def _recorder():
        class Recorder:
            def __init__(self):
                self.events = []

            def handle_keydown(self, key_name, task):
                task.pressed = True
                self.events.append(("down", key_name))

            def handle_keyup(self, key_name, task):
                task.pressed = False
                self.events.append(("up", key_name))

        return Recorder()

    def test_intercept_consumes_the_suppress_flag(self):
        from core.shortcut_keys import darwin_suppress_intercept
        owner = SimpleNamespace(darwin_suppress=False)
        intercept = darwin_suppress_intercept(owner)
        event = object()
        self.assertIs(intercept("keydown", event), event)
        owner.darwin_suppress = True
        self.assertIsNone(intercept("keydown", event))
        self.assertFalse(owner.darwin_suppress)
        self.assertIs(intercept("keydown", event), event)

    def test_keyboard_callback_marks_suppressing_shortcuts(self):
        from pynput import keyboard
        from core.client.shortcut.shortcut_config import Shortcut
        from core.shortcut_keys import darwin_suppress_intercept
        manager = self._manager([Shortcut(key="caps_lock", suppress=True)])
        manager._event_handler = self._recorder()
        manager._keyboard_press(keyboard.Key.caps_lock)
        self.assertTrue(manager.darwin_suppress)
        self.assertEqual(manager._event_handler.events, [("down", "caps_lock")])
        self.assertIsNone(darwin_suppress_intercept(manager)("keydown", object()))
        manager._keyboard_release(keyboard.Key.caps_lock)
        self.assertTrue(manager.darwin_suppress)
        self.assertEqual(manager._event_handler.events[-1], ("up", "caps_lock"))

    def test_keyboard_callback_keeps_normal_keys(self):
        from pynput import keyboard
        from core.client.shortcut.shortcut_config import Shortcut
        manager = self._manager([Shortcut(key="caps_lock", suppress=True)])
        manager._event_handler = self._recorder()
        manager._keyboard_press(keyboard.Key.space)
        self.assertFalse(manager.darwin_suppress)
        self.assertEqual(manager._event_handler.events, [])

    def test_mouse_callback_respects_capture_hold(self):
        from core.client.shortcut.shortcut_config import Shortcut
        manager = self._manager([Shortcut(key="x2", type="mouse", suppress=True)])
        manager._event_handler = self._recorder()
        manager._mouse_click(0, 0, SimpleNamespace(name="x2"), True)
        self.assertTrue(manager.darwin_suppress)
        manager._mouse_click(0, 0, SimpleNamespace(name="x2"), False)
        self.assertTrue(manager.darwin_suppress)
        manager.capture_hold = True
        manager._mouse_click(0, 0, SimpleNamespace(name="x2"), True)
        self.assertFalse(manager.darwin_suppress)

    def test_darwin_intercept_is_attached_on_mac_only(self):
        from core.client.shortcut.shortcut_config import Shortcut
        shortcuts = [Shortcut(key="caps_lock", suppress=True),
                     Shortcut(key="x2", type="mouse", suppress=True)]
        with patch("sys.platform", "darwin"), \
                patch("core.client.shortcut.shortcut_manager.keyboard.Listener") as kb, \
                patch("core.client.shortcut.shortcut_manager.mouse.Listener") as ms:
            manager = self._manager(shortcuts)
            manager.start()
        self.assertIn("darwin_intercept", kb.call_args.kwargs)
        self.assertNotIn("win32_event_filter", kb.call_args.kwargs)
        self.assertIn("darwin_intercept", ms.call_args.kwargs)
        self.assertNotIn("win32_event_filter", ms.call_args.kwargs)
        self.assertIsNotNone(kb.call_args.kwargs["darwin_intercept"]("keydown", object()))

        with patch("sys.platform", "linux"), \
                patch("core.client.shortcut.shortcut_manager.keyboard.Listener") as kb2:
            other = self._manager([Shortcut(key="caps_lock", suppress=True)])
            other.start()
        self.assertNotIn("darwin_intercept", kb2.call_args.kwargs)


class SidedShortcutKeyTests(unittest.TestCase):
    """快捷键区分左右修饰键（左/右 Ctrl、Alt、Shift、Win 各自独立）"""

    def test_capture_keeps_the_modifier_side(self):
        from core.shortcut_keys import KeyCapture, shortcut_label

        def chord(keys):
            capture = KeyCapture()
            for name in keys:
                capture.press(name)
            return capture.release(keys[-1])

        self.assertEqual(chord(["ctrl_r"]), "ctrl_r")
        self.assertEqual(chord(["ctrl_l", "a"]), "ctrl_l+a")
        self.assertEqual(chord(["shift_r"]), "shift_r")
        self.assertEqual(chord(["shift"]), "shift_l")      # Windows 左 Shift 报 shift
        self.assertEqual(chord(["cmd"]), "win_l")          # 左 Win
        self.assertEqual(chord(["cmd_r"]), "win_r")
        self.assertEqual(chord(["alt_gr"]), "alt_r")
        self.assertEqual(chord(["f12"]), "f12")

        self.assertEqual(shortcut_label("ctrl_r+x2"), "右 Ctrl + 鼠标侧键 2")
        self.assertEqual(shortcut_label("shift_l+a"), "左 Shift + A")
        self.assertEqual(shortcut_label(""), "未设置")

    def test_matching_keeps_sides_apart(self):
        from core.shortcut_keys import combo_active, matching_names, normalize_part

        self.assertTrue(combo_active(["ctrl_r"], {"ctrl_r"}))
        self.assertFalse(combo_active(["ctrl_r"], {"ctrl_l"}))
        self.assertFalse(combo_active(["ctrl_l"], {"ctrl_r"}))
        self.assertTrue(combo_active(["ctrl"], {"ctrl_l"}))
        self.assertTrue(combo_active(["ctrl"], {"ctrl_r"}))
        self.assertTrue(combo_active(["shift_l"], {"shift_l"}))
        self.assertFalse(combo_active(["shift_r"], {"shift_l"}))
        self.assertTrue(combo_active(["win_l"], {"win_l"}))
        self.assertFalse(combo_active(["win_r"], {"win_l"}))
        self.assertFalse(combo_active(["ctrl_r", "a"], {"ctrl_r"}))
        self.assertTrue(combo_active(["ctrl_r", "a"], {"ctrl_r", "a"}))

        self.assertEqual(normalize_part("left_ctrl"), "ctrl_l")
        self.assertEqual(normalize_part("right_ctrl"), "ctrl_r")
        self.assertEqual(normalize_part("cmd"), "win")
        self.assertEqual(matching_names("alt"), {"alt", "alt_l", "alt_r"})

    def test_shortcut_normalizes_side_spellings(self):
        from core.client.shortcut.shortcut_config import Shortcut

        self.assertEqual(Shortcut(key="right_ctrl+a").key, "ctrl_r+a")
        self.assertEqual(Shortcut(key="left shift").key, "shift_l")
        self.assertEqual(Shortcut(key="control_l").key, "ctrl_l")
        self.assertEqual(Shortcut(key="lwin+x1").key, "win_l+x1")
        self.assertEqual(Shortcut(key="ctrl").key, "ctrl")
        self.assertEqual(Shortcut(key="caps lock").key, "caps_lock")
        self.assertEqual(Shortcut(key=" ").key, "space")

    def test_name_to_key_maps_canonical_side_names(self):
        from core.client.shortcut.key_mapper import KeyMapper

        expected = {"win_l": "cmd", "win_r": "cmd_r", "shift_l": "shift",
                    "shift_r": "shift_r", "ctrl_l": "ctrl_l", "ctrl_r": "ctrl_r",
                    "alt_l": "alt_l", "alt_r": "alt_r"}
        for name, pynput_name in expected.items():
            key_obj = KeyMapper.name_to_key(name)
            self.assertIsNotNone(key_obj, name)
            self.assertEqual(key_obj.name, pynput_name)

    def test_keyboard_filter_triggers_only_the_matching_side(self):
        from core.client.shortcut.shortcut_manager import (
            ShortcutManager, WM_KEYDOWN, WM_KEYUP)
        from core.client.shortcut.shortcut_config import Shortcut

        class Recorder:
            def __init__(self):
                self.events = []

            def handle_keydown(self, key_name, task):
                if task.pressed:
                    return
                task.pressed = True
                self.events.append(("down", key_name))

            def handle_keyup(self, key_name, task):
                task.pressed = False
                self.events.append(("up", key_name))

        manager = ShortcutHotReloadTests._manager([])
        manager.tasks.clear()
        for key in ("ctrl_l", "ctrl_r", "shift_l+a"):
            manager.tasks[key] = SimpleNamespace(
                shortcut=Shortcut(key=key), pressed=False, released=True)
        manager._pressed_keys = set()
        manager.keyboard_listener = None
        manager._event_handler = Recorder()
        event_filter = manager.create_keyboard_filter()

        event_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0xA3))    # 右 Ctrl
        self.assertEqual(manager._event_handler.events, [("down", "ctrl_r")])

        event_filter(WM_KEYUP, SimpleNamespace(vkCode=0xA3))
        manager._event_handler.events.clear()

        event_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0xA2))    # 左 Ctrl
        self.assertEqual(manager._event_handler.events, [("down", "ctrl_l")])
        event_filter(WM_KEYUP, SimpleNamespace(vkCode=0xA2))
        manager._event_handler.events.clear()

        event_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0x41))    # A
        self.assertEqual(manager._event_handler.events, [])       # 缺少左 Shift
        event_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0xA0))    # 左 Shift
        self.assertEqual(manager._event_handler.events, [("down", "shift_l+a")])
        event_filter(WM_KEYUP, SimpleNamespace(vkCode=0x41))
        self.assertEqual(manager._event_handler.events[-1], ("up", "shift_l+a"))


    def test_mouse_middle_button_is_matched(self):
        from core.client.shortcut.shortcut_manager import (
            ShortcutManager, WM_MBUTTONDOWN, WM_MBUTTONUP, XBUTTON1, WM_XBUTTONDOWN)
        from core.client.shortcut.shortcut_config import Shortcut

        class Recorder:
            def __init__(self):
                self.events = []

            def handle_keydown(self, key_name, task):
                self.events.append(("down", key_name))

        manager = ShortcutHotReloadTests._manager([])
        manager.tasks.clear()
        manager.tasks["middle"] = SimpleNamespace(
            shortcut=Shortcut(key="middle", type="mouse"),
            pressed=False, released=True, is_recording=False)
        manager.mouse_listener = None
        manager._event_handler = Recorder()
        manager._handle_mouse_keyup = lambda button_name, task: (
            manager._event_handler.events.append(("up", button_name)))
        event_filter = manager.create_mouse_filter()

        event_filter(WM_MBUTTONDOWN, SimpleNamespace(mouseData=0))
        event_filter(WM_MBUTTONUP, SimpleNamespace(mouseData=0))
        self.assertEqual(manager._event_handler.events,
                         [("down", "middle"), ("up", "middle")])

        manager._event_handler.events.clear()
        event_filter(WM_XBUTTONDOWN, SimpleNamespace(mouseData=XBUTTON1 << 16))
        self.assertEqual(manager._event_handler.events, [])


class LlamaRuntimeTests(unittest.TestCase):
    """GGUF 引擎的运行库定位、转发层与错误上报"""

    def test_runtime_resolves_the_shared_engine_bin(self):
        from core.tools.llama_runtime import check_llama_bin, main_lib_name
        runtime_dir = ROOT / "core" / "server" / "engines" / "llama"
        if not (runtime_dir / "bin" / main_lib_name()).is_file():
            self.skipTest("本机未放置 llama.cpp 运行库")
        self.assertEqual(check_llama_bin(runtime_dir), runtime_dir / "bin")

    def test_runtime_env_override_wins(self):
        from core.tools.llama_runtime import ENV_BIN, main_lib_name, resolve_llama_bin
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory)
            (override / main_lib_name()).write_bytes(b"")
            with patch.dict(os.environ, {ENV_BIN: str(override)}):
                self.assertEqual(resolve_llama_bin(Path(directory) / "other"), override)

    def test_runtime_reports_a_readable_error_when_missing(self):
        from core.tools.llama_runtime import ENV_BIN, LlamaRuntimeError, main_lib_name, resolve_llama_bin
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "llama"
            with patch.dict(os.environ, {ENV_BIN: ""}):
                with self.assertRaises(LlamaRuntimeError) as caught:
                    resolve_llama_bin(base)
            message = str(caught.exception)
            self.assertIn(main_lib_name(), message)
            self.assertIn(str(base / "bin"), message)
            self.assertIn("重新安装 SAI", message)

    def test_engine_llama_modules_forward_to_the_shared_binding(self):
        for engine in ("fun_asr_gguf", "qwen_asr_gguf", "force_aligner_gguf"):
            source = (ROOT / "core" / "server" / "engines" / engine /
                      "inference" / "llama.py").read_text(encoding="utf-8")
            self.assertIn("from ...llama.llama import", source, engine)
            self.assertNotIn("os.chdir", source, engine)
            self.assertNotIn("Path(__file__)", source, engine)

    def test_engines_no_longer_expect_their_own_runtime_directory(self):
        owned = list(ROOT.glob("core/server/engines/*/inference/bin"))
        self.assertEqual(owned, [])

    def test_worker_writes_a_readable_error_file(self):
        import core.runtime_paths as runtime_paths
        from core.server.worker.worker import RecognizerWorker
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runtime_paths, "DATA_DIR", Path(directory)):
                RecognizerWorker._write_fatal_error(
                    RuntimeError("llama.cpp 运行库缺失：未找到 llama.dll"))
            payload = json.loads(
                (Path(directory) / "logs" / "asr-error.json").read_text(encoding="utf-8"))
            self.assertIn("llama.cpp 运行库缺失", payload["error"])
            self.assertIn("RuntimeError", payload["detail"])

    def test_launcher_reports_the_specific_error_before_the_exit_code(self):
        import gui_launcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            error_path = root / "logs" / "asr-error.json"
            error_path.write_text(
                json.dumps({"error": "识别服务异常：llama.cpp 运行库缺失"}),
                encoding="utf-8")
            launcher = gui_launcher.Launcher.__new__(gui_launcher.Launcher)
            launcher.monitor = None
            launcher.status = Mock()
            launcher.tray_icon = None
            launcher.processes = [SimpleNamespace(
                poll=lambda: 1, sai_role="server", returncode=1)]
            launcher._fail = Mock()
            with patch.object(gui_launcher, "CONFIG", root / "config_gui.json"):
                launcher._check_children()
            message = launcher._fail.call_args[0][0]
            self.assertIn("llama.cpp 运行库缺失", message)
            self.assertIn("server 退出码 1", message)
            self.assertFalse(error_path.exists())


    def test_verify_reports_missing_corrupt_and_repairable_files(self):
        from core.tools.llama_runtime import ENV_BIN, verify_llama_runtime
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "llama"
            (base / "bin").mkdir(parents=True)
            with patch.dict(os.environ, {ENV_BIN: ""}):
                report = verify_llama_runtime(base)
                self.assertIsNone(report.directory)
                self.assertTrue(report.fatal)
                self.assertIn("未安装", report.summary)
                payload = b"MZ" + b"\0" * 2048
                for name in ("ggml.dll", "ggml-base.dll", "llama.dll"):
                    (base / "bin" / name).write_bytes(payload)
                report = verify_llama_runtime(base)
                self.assertFalse(report.fatal)
                self.assertEqual(report.directory, base / "bin")
                self.assertIn("ggml-vulkan.dll", report.missing)
                self.assertTrue(report.needs_repair)
                (base / "bin" / "ggml-vulkan.dll").write_bytes(payload)
                report = verify_llama_runtime(base)
                self.assertFalse(report.needs_repair)
                self.assertIn("正常", report.summary)
                (base / "bin" / "ggml-vulkan.dll").write_bytes(b"")
                report = verify_llama_runtime(base)
                self.assertIn("ggml-vulkan.dll", report.corrupt)
                self.assertTrue(report.fatal)
                self.assertIn("损坏", report.summary)

    def test_runtime_download_source_prefers_the_environment(self):
        from core.tools.llama_runtime import (ENV_SHA256, ENV_URL,
                                              runtime_download_digest,
                                              runtime_download_url)
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "llama"
            with patch.dict(os.environ, {ENV_URL: "http://mirror/runtime.zip",
                                         ENV_SHA256: "AB" * 32}):
                self.assertEqual(runtime_download_url(missing),
                                 "http://mirror/runtime.zip")
                self.assertEqual(runtime_download_digest(missing), "ab" * 32)
            with patch.dict(os.environ, {ENV_URL: "", ENV_SHA256: ""}):
                self.assertIn("llama.cpp/releases/download/b10621/",
                              runtime_download_url(missing))

    def test_install_runtime_unpacks_only_dlls_flat(self):
        from core.model_download import install_llama_runtime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "runtime.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("build/bin/llama.dll", b"MZ" + b"\0" * 2048)
                bundle.writestr("build/bin/ggml-vulkan.dll", b"MZ" + b"\0" * 2048)
                bundle.writestr("llama-cli.exe", b"MZ")
                bundle.writestr("README.md", "hi")
            bin_dir = root / "bin"
            self.assertEqual(install_llama_runtime(archive, bin_dir), 2)
            self.assertTrue((bin_dir / "llama.dll").is_file())
            self.assertFalse((bin_dir / "llama-cli.exe").exists())
            self.assertFalse((bin_dir / "README.md").exists())
            with zipfile.ZipFile(root / "empty.zip", "w") as bundle:
                bundle.writestr("README.md", "hi")
            with self.assertRaises(RuntimeError):
                install_llama_runtime(root / "empty.zip", bin_dir)

    def test_install_runtime_unpacks_macos_tarball_flat(self):
        from core.model_download import install_llama_runtime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "runtime.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                for name, content in (
                        ("llama-b10621/libllama.0.dylib", b"\xcf\xfa\xed\xfe"),
                        ("llama-b10621/libggml.0.dylib", b"\xcf\xfa\xed\xfe"),
                        ("llama-b10621/libggml-cpu.dylib", b"\xcf\xfa\xed\xfe"),
                        ("llama-b10621/libmtmd.0.dylib", b"\xcf\xfa\xed\xfe"),
                        ("llama-b10621/libllama-common.0.dylib", b"\xcf\xfa\xed\xfe"),
                        ("llama-b10621/llama-cli", b"#!/bin/sh"),
                        ("llama-b10621/README.md", b"hi")):
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    bundle.addfile(info, io.BytesIO(content))
                link = tarfile.TarInfo("llama-b10621/libggml.dylib")
                link.type = tarfile.SYMTYPE
                link.linkname = "libggml.0.dylib"
                bundle.addfile(link)
            bin_dir = root / "bin"
            self.assertEqual(
                install_llama_runtime(archive, bin_dir, suffix=".dylib"), 4)
            self.assertTrue((bin_dir / "libllama.0.dylib").is_file())
            self.assertTrue((bin_dir / "libggml.0.dylib").is_file())
            self.assertTrue((bin_dir / "libggml-cpu.dylib").is_file())
            link_path = bin_dir / "libggml.dylib"
            if sys.platform == "win32":
                self.assertTrue(link_path.is_file())
            else:
                self.assertTrue(link_path.is_symlink())
            self.assertFalse((bin_dir / "libmtmd.0.dylib").exists())
            self.assertFalse((bin_dir / "libllama-common.0.dylib").exists())
            self.assertFalse((bin_dir / "llama-cli").exists())
            self.assertFalse((bin_dir / "README.md").exists())
            with tarfile.open(root / "empty.tar.gz", "w:gz") as bundle:
                info = tarfile.TarInfo("README.md")
                info.size = 2
                bundle.addfile(info, io.BytesIO(b"hi"))
            with self.assertRaises(RuntimeError):
                install_llama_runtime(root / "empty.tar.gz", bin_dir,
                                      suffix=".dylib")

    def test_download_runtime_verifies_and_installs_the_archive(self):
        from core.model_download import download_llama_runtime
        from core.tools.llama_runtime import ENV_BIN, ENV_SHA256, ENV_URL

        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as bundle:
            for name in ("ggml.dll", "ggml-base.dll", "llama.dll"):
                bundle.writestr(f"build/bin/{name}", b"MZ" + b"\0" * 2048)
        archive = payload.getvalue()

        class Response:
            headers = {"Content-Length": str(len(archive))}

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                yield archive

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Session:
            trust_env = False

            def __init__(self):
                self.urls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                self.urls.append(url)
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "core" / "server" / "engines" / "llama" / "bin"
            bin_dir.mkdir(parents=True)
            session = Session()
            environment = {ENV_BIN: "", ENV_URL: "http://mirror/runtime.zip",
                           ENV_SHA256: hashlib.sha256(archive).hexdigest()}
            with patch.dict(os.environ, environment), patch(
                    "core.model_download.requests.Session", return_value=session):
                message = download_llama_runtime(root)
            self.assertIn("运行库已修复", message)
            self.assertEqual(session.urls, ["http://mirror/runtime.zip"])
            self.assertTrue((bin_dir / "llama.dll").is_file())
            self.assertFalse((bin_dir / ".runtime-download.part").exists())
            session = Session()
            environment[ENV_SHA256] = "0" * 64
            with patch.dict(os.environ, environment), patch(
                    "core.model_download.requests.Session", return_value=session):
                with self.assertRaises(RuntimeError) as caught:
                    download_llama_runtime(root)
            self.assertIn("SHA256", str(caught.exception))

    def test_launcher_runtime_status_reflects_the_report(self):
        import gui_launcher
        launcher = gui_launcher.Launcher.__new__(gui_launcher.Launcher)
        launcher.runtime_status = Mock()
        launcher.runtime_button = Mock()
        launcher.vars = {"model_type": SimpleNamespace(
            get=lambda: gui_launcher.MODEL_CHOICES["paraformer"])}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(gui_launcher, "ROOT", Path(directory)):
                launcher._update_runtime_status()
                self.assertIn("无需该运行库",
                              launcher.runtime_status.configure.call_args[1]["text"])
                self.assertEqual(launcher.runtime_button.configure.call_args[1]["state"],
                                 "disabled")
                launcher.vars["model_type"] = SimpleNamespace(
                    get=lambda: gui_launcher.MODEL_CHOICES["qwen_asr"])
                launcher._update_runtime_status()
                self.assertIn("未安装",
                              launcher.runtime_status.configure.call_args[1]["text"])
                self.assertEqual(launcher.runtime_status.configure.call_args[1]["style"],
                                 "Danger.TLabel")
                self.assertEqual(launcher.runtime_button.configure.call_args[1]["state"],
                                 "normal")


class KoreanTokenizerFallbackTests(unittest.TestCase):
    """精简安装包不含 soynlp/scipy，韩语分词必须自动退回按字切分。"""

    MODULES = ("core.server.engines.qwen_asr_gguf.inference.aligner",
               "core.server.engines.force_aligner_gguf.inference.aligner")

    def test_korean_tokenizer_falls_back_without_soynlp(self):
        text = "안녕 하세요"
        blocked = {"soynlp": None, "soynlp.tokenizer": None}
        for module_path in self.MODULES:
            with self.subTest(module=module_path):
                module = importlib.import_module(module_path)
                with patch.dict(sys.modules, blocked):
                    processor = module.AlignerProcessor()
                    self.assertEqual(processor.tokenize_korean(text), list(text))

    def test_other_languages_do_not_touch_soynlp(self):
        module = importlib.import_module(self.MODULES[0])
        with patch.dict(sys.modules, {"soynlp": None, "soynlp.tokenizer": None}):
            processor = module.AlignerProcessor()
            self.assertEqual(processor.tokenize("你好 world", "chinese"),
                             ["你", "好", "world"])


class RecordingRetentionTests(unittest.TestCase):
    """录音保留策略：只删除过期音频，日记与其它文件保留。"""

    def _make_tree(self, root, now):
        assets = root / "2026" / "09" / "assets"
        assets.mkdir(parents=True)
        old = assets / "(20260901-090000)旧录音.mp3"
        new = assets / "(20260923-090000)新录音.wav"
        keep = assets / "note.txt"
        diary = root / "2026" / "09" / "01.md"
        for path in (old, new, keep, diary):
            path.write_bytes(b"data")
        stale = now - 10 * 86400
        for path in (old, keep, diary):
            os.utime(path, (stale, stale))
        os.utime(new, (now - 3600, now - 3600))
        return old, new, keep, diary

    def test_cleanup_removes_only_expired_recordings(self):
        from core.client.audio.file_manager import cleanup_old_recordings
        now = time.time()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, keep, diary = self._make_tree(root, now)
            removed = cleanup_old_recordings(root, 3, now=now)
            self.assertEqual(removed, [old])
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertTrue(keep.exists())
            self.assertTrue(diary.exists())

    def test_keep_days_zero_or_invalid_keeps_everything(self):
        from core.client.audio.file_manager import cleanup_old_recordings
        now = time.time()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, _, _ = self._make_tree(root, now)
            for value in (0, -1, "-1", "abc", None):
                self.assertEqual(cleanup_old_recordings(root, value, now=now), [])
            self.assertTrue(old.exists())
            self.assertTrue(new.exists())

    def test_keep_days_accepts_string_numbers(self):
        from core.client.audio.file_manager import cleanup_old_recordings
        now = time.time()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, _, _ = self._make_tree(root, now)
            self.assertEqual(cleanup_old_recordings(root, "3", now=now), [old])
            self.assertTrue(new.exists())

    def test_cleanup_ignores_missing_and_unrelated_files(self):
        from core.client.audio.file_manager import cleanup_old_recordings
        now = time.time()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(cleanup_old_recordings(root, 3, now=now), [])
            stray = root / "2026" / "09" / "assets" / "old.txt"
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"data")
            os.utime(stray, (now - 30 * 86400, now - 30 * 86400))
            self.assertEqual(cleanup_old_recordings(root, 3, now=now), [])
            self.assertTrue(stray.exists())

    def test_audio_folder_prefers_the_latest_month(self):
        from core.client.audio.file_manager import audio_folder
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(audio_folder(root), root)
            older = root / "2026" / "08" / "assets"
            newer = root / "2026" / "09" / "assets"
            for folder in (older, newer):
                folder.mkdir(parents=True)
            self.assertEqual(audio_folder(root), newer)


if __name__ == "__main__":
    unittest.main()
