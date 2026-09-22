"""Tests for the OpenAI-compatible transcription adapter."""
import io
import json
import os
import queue
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from core.api_transcription_config import validate_api_settings
from core.server.engines.openai_asr import APIConfig, OpenAIASREngine


class APITranscriptionTests(unittest.TestCase):
    def test_real_http_roundtrip_on_loopback(self):
        requests_seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests_seen.append((self.path, self.headers, body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"text": "API test"}).encode())

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            engine = OpenAIASREngine(APIConfig(
                f"http://127.0.0.1:{server.server_port}/v1", "test-model", "test-key"))
            stream = engine.create_stream()
            stream.accept_waveform(16000, np.zeros(1600))
            engine.decode_stream(stream)
            self.assertEqual(stream.result.text, "API test")
            path, headers, body = requests_seen[0]
            self.assertEqual(path, "/v1/audio/transcriptions")
            self.assertEqual(headers["Authorization"], "Bearer test-key")
            self.assertIn(b"RIFF", body)
            self.assertIn(b"test-model", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_loopback_ignores_environment_proxy(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"text": "direct"}).encode())

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {
                    "HTTP_PROXY": "http://127.0.0.1:9",
                    "http_proxy": "http://127.0.0.1:9",
                    "NO_PROXY": "", "no_proxy": ""}):
                engine = OpenAIASREngine(APIConfig(
                    f"http://127.0.0.1:{server.server_port}/v1", "m", "k", 10))
                stream = engine.create_stream()
                stream.accept_waveform(16000, np.zeros(1600, dtype=np.float32))
                engine.decode_stream(stream)
            self.assertEqual(stream.result.text, "direct")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_failed_segment_cannot_produce_partial_final_text(self):
        from config_server import ServerConfig
        from core.server.state import WorkerState
        from core.server.worker.task_handler import TaskHandler
        handler = TaskHandler(queue.Queue(), queue.Queue(), [], WorkerState())
        from unittest.mock import Mock
        handler.pipeline = Mock()
        handler.pipeline.process.side_effect = RuntimeError("语音 API HTTP 401")
        task = SimpleNamespace(task_id="test", socket_id="socket", type="mic", is_final=False)
        with patch.object(ServerConfig, "model_type", "openai_api"):
            handler.handle_audio_task(task)
            self.assertTrue(handler.queue_out.get().error)
            task.is_final = True
            handler.handle_audio_task(task)
        result = handler.queue_out.get()
        self.assertTrue(result.is_final)
        self.assertEqual(result.text, "")
        self.assertTrue(result.error)
        handler.pipeline.process.assert_called_once()
        self.assertNotIn("test", handler.state.sessions)

    def test_api_loader_needs_no_local_models(self):
        from config_server import ServerConfig
        from core.server.worker.model_loader import ModelLoader
        from core.server.worker.check_model import check_model
        with patch.object(ServerConfig, "model_type", "openai_api"):
            check_model()
            loader = ModelLoader()
            loader.load()
            self.assertIsInstance(loader.recognizer, OpenAIASREngine)
            self.assertIsNone(loader.punc_model)
            self.assertIsNone(loader.aligner)
            loader.cleanup()

    @patch("core.server.engines.openai_asr.requests.post")
    def test_errors_do_not_expose_response_body_or_key(self, post):
        response = post.return_value
        response.status_code = 401
        response.text = "secret"
        engine = OpenAIASREngine(APIConfig("https://example.test/v1", "asr", "secret"))
        stream = engine.create_stream()
        stream.accept_waveform(16000, np.zeros(1600))
        with self.assertRaisesRegex(RuntimeError, "401") as raised:
            engine.decode_stream(stream)
        self.assertNotIn("secret", str(raised.exception))
        response.close.assert_called_once()

    def test_validation(self):
        self.assertEqual(
            validate_api_settings("https://example.test/v1/", "whisper-1", "30"),
            ("https://example.test/v1", "whisper-1", 30.0),
        )
        self.assertEqual(
            validate_api_settings("http://localhost:8000/v1", "m", 60),
            ("http://localhost:8000/v1", "m", 60.0),
        )
        with self.assertRaises(ValueError):
            validate_api_settings("http://example.test/v1", "x", 60)
        with self.assertRaises(ValueError):
            validate_api_settings("http://127.0.0.1:8000/v1", "", 60)

    def test_validation_allows_trusted_plain_http(self):
        self.assertEqual(
            validate_api_settings("http://176.4.88.88/v1/", "m", "60", allow_http=True),
            ("http://176.4.88.88/v1", "m", 60.0),
        )
        with self.assertRaises(ValueError):
            validate_api_settings("http://176.4.88.88/v1", "m", 60)
        with self.assertRaises(ValueError):
            validate_api_settings("http://192.168.1.10:8000/v1", "m", 60)

    @patch("core.server.engines.openai_asr.requests.post")
    def test_lan_endpoints_bypass_the_system_proxy(self, post):
        response = post.return_value
        response.status_code = 200
        response.json.return_value = {"text": "ok"}
        direct = {"http": None, "https": None}
        for url, allow_http in (
                ("http://176.4.88.88/v1", True),
                ("http://192.168.1.20:9000/v1", True),
                ("https://192.168.1.20/v1", False),
                ("http://127.0.0.1:8000/v1", False)):
            with self.subTest(url=url):
                post.reset_mock()
                engine = OpenAIASREngine(APIConfig(url, "m", "k", 30, allow_http))
                stream = engine.create_stream()
                stream.accept_waveform(16000, np.zeros(1600, dtype=np.float32))
                engine.decode_stream(stream)
                self.assertEqual(post.call_args.kwargs["proxies"], direct)
        post.reset_mock()
        engine = OpenAIASREngine(APIConfig("https://api.openai.com/v1", "m", "k"))
        stream = engine.create_stream()
        stream.accept_waveform(16000, np.zeros(1600, dtype=np.float32))
        engine.decode_stream(stream)
        self.assertIsNone(post.call_args.kwargs["proxies"])

    @patch("core.server.engines.openai_asr.requests.post")
    def test_posts_wav_and_reads_text(self, post):
        response = post.return_value
        response.status_code = 200
        response.json.return_value = {"text": "你好"}
        engine = OpenAIASREngine(APIConfig(
            "http://127.0.0.1:8000/v1", "whisper-1", "secret", 12))
        stream = engine.create_stream()
        stream.accept_waveform(16000, np.zeros(1600, dtype=np.float32))
        engine.decode_stream(stream, context="SAI", language="chinese")
        call = post.call_args
        self.assertEqual(call.args[0], "http://127.0.0.1:8000/v1/audio/transcriptions")
        self.assertEqual(call.kwargs["data"]["model"], "whisper-1")
        self.assertEqual(call.kwargs["data"]["language"], "zh")
        self.assertEqual(call.kwargs["data"]["prompt"], "SAI")
        filename, content, content_type = call.kwargs["files"]["file"]
        self.assertEqual((filename, content_type), ("recording.wav", "audio/wav"))
        with wave.open(io.BytesIO(content), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels()), (16000, 1))
        self.assertEqual(stream.result.text, "你好")


if __name__ == "__main__":
    unittest.main()
