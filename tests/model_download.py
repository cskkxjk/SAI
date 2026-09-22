"""On-demand model download and lightweight packaging regressions."""
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.model_download import (
    DOWNLOADS, DownloadCancelled, download_model, missing_files, model_files,
)


class DownloadTests(unittest.TestCase):
    def test_quantizations_share_encoders_but_not_decoder(self):
        q4 = model_files("qwen_asr", "q4_k")
        q5 = model_files("qwen_asr", "q5_k")
        self.assertEqual(q4[:2], q5[:2])
        self.assertTrue(q4[-1].endswith(".q4_k.gguf"))
        self.assertTrue(q5[-1].endswith(".q5_k.gguf"))
        for relative in q5:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"model")
        self.assertEqual(missing_files(self.root, "qwen_asr", "q5_k"), [])
        self.assertEqual(missing_files(self.root, "qwen_asr", "q4_k"), [q4[-1]])

    def test_legacy_decoder_is_reused_only_after_verification(self):
        self.entry["relative"] = "models/test/qwen3_asr_llm.q5_k.gguf"
        target = self.root / self.entry["relative"]
        target.parent.mkdir(parents=True)
        legacy = target.with_name("qwen3_asr_llm.gguf")
        legacy.write_bytes(self.data)
        download_model("qwen_asr", self.root)
        self.assertEqual(target.read_bytes(), self.data)
        self.session.get.assert_not_called()
        self.assertFalse(legacy.exists())

    def test_download_passes_selected_quantization(self):
        download_model("qwen_asr", self.root, quantization="q4_k")
        from core.model_download import fetch_manifest
        fetch_manifest.assert_called_once_with(self.session, "qwen_asr", "q4_k")

    def test_server_uses_selected_decoder(self):
        import json
        import os
        import subprocess
        import sys
        for quant in ("q4_k", "q5_k"):
            (self.root / "config_gui.json").write_text(
                json.dumps({"qwen_quantization": quant}), encoding="utf-8")
            env = dict(os.environ, SAI_DATA_DIR=str(self.root))
            result = subprocess.run(
                [sys.executable, "-c",
                 "from config_server import Qwen3ASRGGUFArgs; print(Qwen3ASRGGUFArgs.llm_fn)"],
                env=env, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), f"qwen3_asr_llm.{quant}.gguf")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b"test model"
        self.entry = dict(repo="owner/model", source="model.onnx", relative="models/test/model.onnx",
                          size=len(self.data), digest=hashlib.sha256(self.data).hexdigest(),
                          revision="test-commit")
        self.target = self.root / self.entry["relative"]
        self.manifest = patch("core.model_download.fetch_manifest", return_value=[self.entry])
        self.manifest.start()
        self.addCleanup(self.manifest.stop)
        self.session_patch = patch("core.model_download.requests.Session")
        self.session = self.session_patch.start().return_value.__enter__.return_value
        self.addCleanup(self.session_patch.stop)
        self.response = self.session.get.return_value.__enter__.return_value
        self.response.iter_content.return_value = [self.data]

    def test_atomic_download_and_reuse(self):
        download_model("sensevoice", self.root)
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertFalse(self.session.trust_env)
        self.assertFalse(self.target.with_suffix(".onnx.part").exists())
        self.session.get.reset_mock()
        download_model("sensevoice", self.root)
        self.session.get.assert_not_called()

    def test_corrupt_existing_file_is_replaced(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"x" * len(self.data))
        download_model("sensevoice", self.root)
        self.assertEqual(self.target.read_bytes(), self.data)

    def test_bad_checksum_preserves_existing_file(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"old")
        self.response.iter_content.return_value = [b"x" * len(self.data)]
        with self.assertRaisesRegex(RuntimeError, "校验失败"):
            download_model("sensevoice", self.root)
        self.assertEqual(self.target.read_bytes(), b"old")
        self.assertFalse(self.target.with_suffix(".onnx.part").exists())

    def test_cancel_removes_partial_download(self):
        cancel = threading.Event()
        def chunks():
            yield b"test"
            cancel.set()
            yield b" model"
        self.response.iter_content.side_effect = lambda _: chunks()
        with self.assertRaises(DownloadCancelled):
            download_model("sensevoice", self.root, cancel=cancel)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_suffix(".onnx.part").exists())

    def test_network_error_removes_partial_file(self):
        def chunks():
            yield b"test"
            raise OSError("network interrupted")
        self.response.iter_content.side_effect = lambda _: chunks()
        with self.assertRaises(OSError):
            download_model("sensevoice", self.root)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_suffix(".onnx.part").exists())

    def test_disk_full_does_not_download(self):
        with patch("core.model_download.shutil.disk_usage", return_value=MagicMock(free=0)):
            with self.assertRaisesRegex(OSError, "空间不足"):
                download_model("sensevoice", self.root)
        self.session.get.assert_not_called()

    def test_unwritable_directory_reports_error(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                download_model("sensevoice", self.root)

    def test_path_cannot_escape_models(self):
        self.entry["relative"] = "../outside.onnx"
        with self.assertRaisesRegex(RuntimeError, "拒绝写入"):
            download_model("sensevoice", self.root)
        self.session.get.assert_not_called()

    def test_model_catalog_matches_gui_and_includes_punctuation(self):
        from gui_launcher import MODEL_INFO
        for key, info in MODEL_INFO.items():
            self.assertEqual(tuple(info["files"]), model_files(key))
        self.assertEqual(missing_files(self.root, "openai_api"), [])
        self.assertEqual(len(model_files("sensevoice")), 3)
        self.assertEqual(len(model_files("fun_asr_nano")), 4)
        for relative in model_files("sensevoice"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        self.assertEqual(missing_files(self.root, "sensevoice"), [])
        (self.root / model_files("sensevoice")[0]).write_bytes(b"")
        self.assertEqual(len(missing_files(self.root, "sensevoice")), 1)

    def test_installer_does_not_bundle_model_sources(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "build-desktop.spec").read_text(encoding="utf-8")
        script = (root / "installer/SAI.iss").read_text(encoding="utf-8")
        self.assertNotIn("MODEL_INFO", spec)
        self.assertIn("DiskSpanning=no", script)
        self.assertNotIn('Source: "{#SourceDir}\\models\\', script)


if __name__ == "__main__":
    unittest.main()
