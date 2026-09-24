"""Installation data isolation and upgrade preservation tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.runtime_paths import (data_directory, initialize_user_data,
                                read_pointer, write_pointer)
from core.tools.data_migration import move_data, validate_target


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

    def test_pointer_file_next_to_program_wins(self):
        custom = self.root / "Data"
        custom.mkdir()
        (self.app / "data-dir.txt").write_text(str(custom), encoding="utf-8")
        self.assertEqual(data_directory(self.app), custom)

    def test_pointer_file_in_user_directory_is_used(self):
        (self.app / "installed.flag").touch()
        custom = self.root / "PortableData"
        custom.mkdir()
        self.data.mkdir(parents=True)
        (self.data / "data-dir.txt").write_bytes(
            b"\xef\xbb\xbf" + str(custom).encode("utf-8"))
        self.assertEqual(data_directory(self.app), custom)

    def test_pointer_with_missing_parent_is_ignored(self):
        (self.app / "data-dir.txt").write_text(str(self.root / "gone" / "SAI"),
                                               encoding="utf-8")
        self.assertEqual(data_directory(self.app), self.app)

    def test_environment_override_beats_pointer(self):
        custom = self.root / "Data"
        custom.mkdir()
        (self.app / "data-dir.txt").write_text(str(custom), encoding="utf-8")
        with patch.dict(os.environ, {"SAI_DATA_DIR": str(self.root / "env")}):
            self.assertEqual(data_directory(self.app), self.root / "env")

    def test_write_pointer_records_every_location(self):
        target = self.root / "Moved"
        target.mkdir()
        written = write_pointer(target, [self.app, self.data])
        self.assertEqual(len(written), 2)
        self.assertEqual(read_pointer(self.app), target)
        self.assertEqual(read_pointer(self.data), target)
        self.assertTrue((self.app / "data-dir.txt").read_bytes()
                        .startswith(b"\xef\xbb\xbf"))

    def test_write_pointer_reports_unwritable_target(self):
        blocker = self.app / "blocker.txt"
        blocker.write_text("x")
        with self.assertRaises(OSError):
            write_pointer(self.root / "Moved", [blocker])

    def test_move_data_moves_and_merges(self):
        source = self.root / "old"
        target = self.root / "new"
        (source / "2026" / "09" / "assets").mkdir(parents=True)
        (source / "2026" / "09" / "assets" / "a.mp3").write_bytes(b"a")
        (source / "logs").mkdir()
        (source / "logs" / "client.log").write_text("log")
        (source / "config_gui.json").write_text("mine")
        (source / "data-dir.txt").write_text("keep")
        (target / "2026" / "09" / "assets").mkdir(parents=True)
        (target / "2026" / "09" / "assets" / "b.mp3").write_bytes(b"b")
        report = move_data(source, target)
        self.assertTrue((target / "2026" / "09" / "assets" / "a.mp3").exists())
        self.assertTrue((target / "2026" / "09" / "assets" / "b.mp3").exists())
        self.assertEqual((target / "config_gui.json").read_text(), "mine")
        self.assertEqual((target / "logs" / "client.log").read_text(), "log")
        self.assertEqual((source / "data-dir.txt").read_text(), "keep")
        self.assertFalse((source / "2026").exists())
        self.assertEqual(report["failed"], [])

    def test_move_data_keeps_existing_target_files(self):
        source = self.root / "old"
        target = self.root / "new"
        source.mkdir()
        target.mkdir()
        (source / "hot.txt").write_text("old")
        (target / "hot.txt").write_text("new")
        report = move_data(source, target)
        self.assertEqual((target / "hot.txt").read_text(), "new")
        self.assertIn("hot.txt", report["skipped"])

    def test_validate_target_rejects_unsafe_choices(self):
        source = self.root / "data"
        source.mkdir()
        self.assertTrue(validate_target(source, source))
        self.assertTrue(validate_target(source, source / "inner"))
        self.assertTrue(validate_target(source, self.root))
        self.assertTrue(validate_target(source, self.app, app_dir=self.app))
        file_path = self.root / "file.txt"
        file_path.write_text("x")
        self.assertTrue(validate_target(source, file_path))
        good = self.root / "other" / "SAI"
        self.assertIsNone(validate_target(source, good, app_dir=self.app))
        self.assertTrue(good.is_dir())

    def test_installer_offers_data_directory_page(self):
        script = (Path(__file__).resolve().parent.parent / "installer"
                  / "SAI.iss").read_text(encoding="utf-8")
        self.assertIn("CreateInputDirPage", script)
        self.assertIn("data-dir.txt", script)
        self.assertIn("SaveStringsToUTF8File", script)
        self.assertIn("Utf8Decode", script)

    def test_launcher_passes_data_directory_to_children(self):
        import gui_launcher
        launcher = gui_launcher.Launcher.__new__(gui_launcher.Launcher)
        launcher.processes = []
        launcher.ready_files = {}
        launcher.recording_files = {}
        config = self.root / "config_gui.json"
        with patch.object(gui_launcher, "CONFIG", config), \
                patch.object(gui_launcher.subprocess, "Popen") as popen:
            popen.return_value = Mock()
            launcher._spawn("server")
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["SAI_DATA_DIR"], str(gui_launcher.DATA_DIR))

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
