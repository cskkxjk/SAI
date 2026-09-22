"""Installation data isolation and upgrade preservation tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.runtime_paths import data_directory, initialize_user_data


class InstallerPathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "Program Files" / "SAI"
        self.data = self.root / "LocalAppData" / "SAI"
        self.app.mkdir(parents=True)
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "LocalAppData")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.override = patch.dict(os.environ)
        self.override.start()
        self.addCleanup(self.override.stop)
        os.environ.pop("SAI_DATA_DIR", None)

    def test_portable_uses_executable_directory(self):
        self.assertEqual(data_directory(self.app), self.app)

    def test_installed_uses_user_directory(self):
        (self.app / "installed.flag").touch()
        self.assertEqual(data_directory(self.app), self.data)

    def test_explicit_test_directory_is_supported(self):
        with patch.dict(os.environ, {"SAI_DATA_DIR": str(self.root / "test")}):
            self.assertEqual(data_directory(self.app), self.root / "test")

    def test_first_run_copies_defaults_without_copying_models(self):
        (self.app / "config_gui.json").write_text('{"model_type":"sensevoice"}')
        (self.app / "hot.txt").write_text("example")
        roles = self.app / "LLM" / "Default"
        roles.mkdir(parents=True)
        (roles / "role.py").write_text("enabled = False")
        models = self.app / "models"
        models.mkdir()
        (models / "model.onnx").write_bytes(b"not a model")
        initialize_user_data(self.app, self.data)
        self.assertEqual(json.loads((self.data / "config_gui.json").read_text())["model_type"],
                         "sensevoice")
        self.assertTrue((self.data / "LLM" / "Default" / "role.py").exists())
        self.assertFalse((self.data / "models").exists())

    def test_upgrade_preserves_settings_hotwords_and_recordings(self):
        self.data.mkdir(parents=True)
        for name in ("config_gui.json", "hot.txt", "hot-rule.txt", "hot-server.txt"):
            (self.app / name).write_text("factory default")
            (self.data / name).write_text("user content")
        (self.data / "2026").mkdir()
        (self.data / "2026" / "retained.wav").write_bytes(b"retained")
        initialize_user_data(self.app, self.data)
        initialize_user_data(self.app, self.data)
        self.assertEqual((self.data / "config_gui.json").read_text(), "user content")
        self.assertEqual((self.data / "hot-rule.txt").read_text(), "user content")
        self.assertEqual((self.data / "2026" / "retained.wav").read_bytes(), b"retained")

    def test_portable_initialization_does_not_modify_files(self):
        (self.app / "hot.txt").write_text("mine")
        initialize_user_data(self.app, self.app)
        self.assertEqual((self.app / "hot.txt").read_text(), "mine")

    def test_shipped_defaults_match_the_bundled_engine(self):
        repository = Path(__file__).resolve().parent.parent
        default = json.loads((repository / "installer" / "config_gui.json").read_text(encoding="utf-8"))
        self.assertEqual(default["model_type"], "fun_asr_nano")

    def test_roles_load_from_user_data_and_reload_without_bytecode_cache(self):
        from core.client.llm.llm_role_loader import RoleLoader
        import config_client
        roles = self.data / "LLM"
        roles.mkdir(parents=True)
        role = roles / "default.py"
        role.write_text("provider = 'ollama'\nmodel = 'user-model-a'\n", encoding="utf-8")
        with patch.object(config_client, "BASE_DIR", str(self.data)), patch.dict("sys.modules"):
            loader = RoleLoader()
            self.assertEqual(loader.get_default_role().model, "user-model-a")
            role.write_text("provider = 'ollama'\nmodel = 'user-model-b'\n", encoding="utf-8")
            self.assertTrue(loader.reload_role(str(role))[0])
            self.assertEqual(loader.get_default_role().model, "user-model-b")
