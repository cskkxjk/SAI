"""Release assets exclude documentation images, but retain engine resources."""

import ast
import sys
from pathlib import Path
from shutil import copy2, copytree, ignore_patterns
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from zip_release import create_file_list


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAssetsTests(unittest.TestCase):
    def test_zip_only_filters_top_level_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "SAI"
            names = (
                "assets/icon.ico", "assets/icon-recording.ico",
                "assets/demo.png", "assets/BUILD_GUIDE.md",
                "assets/nested/icon.ico",
                "core/engine/assets/korean_dict_jieba.dict", "SAI.exe",
            )
            for name in names:
                path = dist / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            for client_only in (False, True):
                with self.subTest(client_only=client_only):
                    files, listing = create_file_list(
                        dist, root / "files.txt", client_only)
                    expected = {
                        str(Path(dist.name) / name) for name in (
                            "assets/icon.ico",
                            "assets/icon-recording.ico",
                            "core/engine/assets/korean_dict_jieba.dict",
                            "SAI.exe",
                        )
                    }
                    self.assertEqual(set(files), expected)
                    self.assertEqual(set(listing.read_text().splitlines()), expected)

    def test_desktop_copy_step_only_copies_icon(self):
        tree = ast.parse((ROOT / "build-desktop.spec").read_text(encoding="utf-8"))
        start = next(i for i, node in enumerate(tree.body)
                     if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "destination"
                             for t in node.targets))
        copy_step = ast.Module(body=tree.body[start:], type_ignores=[])
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            exec(compile(copy_step, "build-desktop.spec", "exec"), {
                "root": ROOT, "coll": SimpleNamespace(name=destination),
                "Path": Path, "copy2": copy2, "copytree": copytree,
                "ignore_patterns": ignore_patterns,
            })
            self.assertEqual(
                sorted(p.name for p in (destination / "assets").iterdir()),
                ["icon-recording.ico", "icon.ico"])
            self.assertTrue((destination / "core/server/engines/qwen_asr_gguf/"
                             "inference/assets/korean_dict_jieba.dict").is_file())

    def test_desktop_build_excludes_korean_tokenizer_stack(self):
        """soynlp/scipy (and the optional Cython/lxml chain) only serve the
        aligner's Korean tokenizer, which falls back to character splitting
        when the import fails; excluding them keeps the installer small."""
        tree = ast.parse((ROOT / "build-desktop.spec").read_text(encoding="utf-8"))
        excluded = {
            item
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword) and node.arg == "excludes"
            for item in ast.literal_eval(node.value)
        }
        for name in ("soynlp", "scipy", "Cython", "cython", "lxml"):
            with self.subTest(name=name):
                self.assertIn(name, excluded)

    def test_installer_uses_solid_max_compression(self):
        script = (ROOT / "installer" / "SAI.iss").read_text(encoding="utf-8")
        self.assertIn("Compression=lzma2/ultra64", script)
        self.assertIn("SolidCompression=yes", script)

    def test_installer_deletes_legacy_unused_packages(self):
        script = (ROOT / "installer" / "SAI.iss").read_text(encoding="utf-8")
        self.assertIn("[InstallDelete]", script)
        for name in ("scipy", "scipy.libs", "Cython", "lxml", "soynlp",
                     "pyximport", "pydoc_data"):
            with self.subTest(name=name):
                self.assertIn(r'Name: "{app}\internal\%s"' % name, script)

    def test_legacy_builds_copy_icon_without_linking_assets(self):
        for filename in ("build.spec", "build-client.spec"):
            with self.subTest(filename=filename):
                tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
                assignments = {
                    node.targets[0].id: ast.literal_eval(node.value)
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in ("my_files", "link_folders")
                }
                self.assertIn("assets/icon.ico", assignments["my_files"])
                self.assertNotIn("assets", assignments["link_folders"])


class ExpatPinTests(unittest.TestCase):
    def test_release_specs_pin_expat_binary(self):
        for filename in ("build-desktop.spec", "build-macos.spec"):
            with self.subTest(filename=filename):
                tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
                names = {node.name for node in tree.body
                         if isinstance(node, ast.FunctionDef)}
                self.assertIn("pin_expat_binary", names)
                calls = [node for node in ast.walk(tree)
                         if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Name)
                         and node.func.id == "pin_expat_binary"]
                self.assertTrue(calls)

    def test_expat_pin_prefers_build_environment_copy(self):
        for filename in ("build-desktop.spec", "build-macos.spec"):
            tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
            function = next(node for node in tree.body
                            if isinstance(node, ast.FunctionDef)
                            and node.name == "pin_expat_binary")
            module = ast.Module(body=[function], type_ignores=[])
            for platform, library in (("win32", "libexpat.dll"),
                                      ("darwin", "libexpat.1.dylib")):
                with self.subTest(spec=filename, platform=platform):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        (root / "DLLs").mkdir()
                        (root / "Library" / "bin").mkdir(parents=True)
                        correct = root / "Library" / "bin" / library
                        correct.write_bytes(b"correct")
                        wrong = root / "elsewhere" / library
                        wrong.parent.mkdir()
                        wrong.write_bytes(b"wrong")
                        keep = root / "elsewhere" / "keep.dll"
                        keep.write_bytes(b"keep")
                        analysis = SimpleNamespace(binaries=[
                            (library, str(wrong), "BINARY"),
                            ("keep.dll", str(keep), "BINARY"),
                        ])
                        fake_sys = SimpleNamespace(
                            platform=platform, base_prefix=str(root),
                            executable=str(root / "python.exe"))
                        fake_pyexpat = SimpleNamespace(
                            __file__=str(root / "DLLs" / "pyexpat.pyd"))
                        scope = {"Path": Path, "sys": fake_sys}
                        with mock.patch.dict(sys.modules,
                                             {"pyexpat": fake_pyexpat}):
                            exec(compile(module, filename, "exec"), scope)
                            chosen = scope["pin_expat_binary"](analysis)
                        self.assertEqual(chosen, correct)
                        self.assertEqual(
                            [entry for entry in analysis.binaries
                             if Path(str(entry[0])).name == library],
                            [(library, str(correct), "BINARY")])
                        self.assertIn(
                            ("keep.dll", str(keep), "BINARY"),
                            analysis.binaries)


if __name__ == "__main__":
    unittest.main()
