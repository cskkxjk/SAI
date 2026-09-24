# coding: utf-8
"""更新检查与安装包下载的纯逻辑测试，不访问网络。"""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

from core import update_checker

INSTALLER_CONTENT = b"setup-binary"


def release_payload(version="1.0.3", digest=True, prerelease=False, draft=False):
    """构造一份 GitHub Release JSON"""
    asset = {
        "name": f"SAI-{version}-Setup.exe",
        "browser_download_url": f"https://example.test/SAI-{version}-Setup.exe",
        "size": len(INSTALLER_CONTENT),
    }
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(INSTALLER_CONTENT).hexdigest()
    return {
        "tag_name": f"v{version}",
        "name": f"SAI {version}",
        "body": "### 更新\n- 测试说明",
        "html_url": f"https://github.com/cskkxjk/SAI/releases/tag/v{version}",
        "published_at": "2026-09-24T00:00:00Z",
        "draft": draft,
        "prerelease": prerelease,
        "assets": [
            {"name": "SHA256SUMS.txt",
             "browser_download_url": "https://example.test/SHA256SUMS.txt"},
            asset,
        ],
    }


class FakeResponse:
    def __init__(self, payload=None, content=b"", headers=None, text="",
                 error=None):
        self.payload = payload
        self.content = content
        self.headers = headers or {}
        self.text = text
        self.error = error
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    def json(self):
        return self.payload

    def iter_content(self, size):
        for index in range(0, len(self.content), size):
            yield self.content[index:index + size]


class FakeSession:
    def __init__(self, response=None):
        self.response = response
        self.trust_env = False
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if self.response is None:
            raise AssertionError("不应发起网络请求")
        return self.response


class FakeRoutingSession:
    """按 URL 返回不同响应；值为异常时抛出，用于验证回退逻辑"""

    def __init__(self, responses):
        self.responses = responses
        self.trust_env = False
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        result = self.responses.get(url)
        if result is None:
            raise AssertionError(f"未预期的请求: {url}")
        if isinstance(result, Exception):
            raise result
        return result


ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Release notes from SAI</title>
  <entry>
    <id>tag:github.com,2008:Repository/1234567890/v1.0.3</id>
    <title>SAI 1.0.3</title>
    <link rel="alternate" type="text/html"
          href="https://github.com/cskkxjk/SAI/releases/tag/v1.0.3"/>
    <updated>2026-09-24T00:00:00Z</updated>
    <content type="html">&lt;h3&gt;本版更新&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;修复 A &amp;amp; B&lt;/li&gt;&lt;li&gt;优化 C&lt;/li&gt;&lt;/ul&gt;</content>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/1234567890/v1.0.2</id>
    <title>SAI 1.0.2</title>
    <link rel="alternate" type="text/html"
          href="https://github.com/cskkxjk/SAI/releases/tag/v1.0.2"/>
    <updated>2026-09-23T00:00:00Z</updated>
    <content type="html">&lt;p&gt;旧版本&lt;/p&gt;</content>
  </entry>
</feed>
"""


class UpdateAtomTests(unittest.TestCase):
    def test_parse_atom_reads_latest_entry(self):
        info = update_checker.parse_atom(ATOM_XML, "1.0.2")
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "1.0.3")
        self.assertEqual(info.tag, "v1.0.3")
        self.assertEqual(info.name, "SAI 1.0.3")
        self.assertIn("本版更新", info.notes)
        self.assertIn("- 修复 A & B", info.notes)
        self.assertIn("- 优化 C", info.notes)
        self.assertEqual(info.page_url,
                         "https://github.com/cskkxjk/SAI/releases/tag/v1.0.3")
        self.assertEqual(info.installer_name, "SAI-1.0.3-Setup.exe")
        self.assertEqual(
            info.installer_url,
            "https://github.com/cskkxjk/SAI/releases/download/v1.0.3/"
            "SAI-1.0.3-Setup.exe")
        self.assertEqual(info.published_at, "2026-09-24T00:00:00Z")
        self.assertEqual(info.installer_sha256, "")
        self.assertTrue(info.can_install)

    def test_parse_atom_ignores_same_older_and_prerelease(self):
        self.assertIsNone(update_checker.parse_atom(ATOM_XML, "1.0.3"))
        self.assertIsNone(update_checker.parse_atom(ATOM_XML, "1.0.4"))
        prerelease = ATOM_XML.replace("v1.0.3", "v1.0.3-rc1")
        self.assertIsNone(update_checker.parse_atom(prerelease, "1.0.2"))
        self.assertIsNone(update_checker.parse_atom("<feed/>", "1.0.2"))
        self.assertIsNone(update_checker.parse_atom("not xml", "1.0.2"))

    def test_fetch_falls_back_to_atom_when_api_limited(self):
        limited = requests.HTTPError("403 rate limit exceeded")
        atom_url = "https://example.test/releases.atom"
        session = FakeRoutingSession({
            "https://example.test/latest": limited,
            atom_url: FakeResponse(text=ATOM_XML),
        })
        info = update_checker.fetch_latest_release(
            "1.0.2", session=session, api_url="https://example.test/latest",
            atom_url=atom_url)
        self.assertEqual(info.version, "1.0.3")
        self.assertEqual(session.urls,
                         ["https://example.test/latest", atom_url])
        self.assertTrue(session.trust_env)

    def test_fetch_prefers_api_over_atom(self):
        atom_url = "https://example.test/releases.atom"
        session = FakeRoutingSession({
            "https://example.test/latest":
                FakeResponse(payload=release_payload()),
            atom_url: FakeResponse(text=ATOM_XML),
        })
        info = update_checker.fetch_latest_release(
            "1.0.2", session=session, api_url="https://example.test/latest",
            atom_url=atom_url)
        self.assertEqual(info.installer_sha256,
                         hashlib.sha256(INSTALLER_CONTENT).hexdigest())
        self.assertEqual(session.urls, ["https://example.test/latest"])

    def test_fetch_reports_error_when_both_sources_fail(self):
        atom_url = "https://example.test/releases.atom"
        session = FakeRoutingSession({
            "https://example.test/latest": requests.HTTPError("403"),
            atom_url: requests.ConnectionError("boom"),
        })
        with self.assertRaises(RuntimeError) as caught:
            update_checker.fetch_latest_release(
                "1.0.2", session=session, api_url="https://example.test/latest",
                atom_url=atom_url)
        self.assertIn("releases.atom", str(caught.exception))

    def test_failed_check_retries_after_attempt_interval(self):
        state = {}
        update_checker.mark_attempted(state, now=1000.0)
        self.assertFalse(update_checker.should_check(state, now=1000.0 + 60))
        self.assertTrue(update_checker.should_check(
            state, now=1000.0 + update_checker.ATTEMPT_INTERVAL + 1))
        update_checker.mark_checked(state, now=2000.0)
        self.assertNotIn("last_attempt", state)
        self.assertFalse(update_checker.should_check(state, now=2000.0 + 60))


class UpdateCheckerTests(unittest.TestCase):
    def test_parse_version_handles_prefix_and_suffix(self):
        self.assertEqual(update_checker.parse_version("v1.0.2"), (1, 0, 2))
        self.assertEqual(update_checker.parse_version("1.0.2+build.5"), (1, 0, 2))
        self.assertEqual(update_checker.parse_version("1.0"), (1, 0))
        self.assertIsNone(update_checker.parse_version("nightly"))
        self.assertIsNone(update_checker.parse_version(None))

    def test_is_newer_compares_numeric_parts(self):
        self.assertTrue(update_checker.is_newer("1.0.10", "1.0.9"))
        self.assertTrue(update_checker.is_newer("v1.1", "1.0.9"))
        self.assertFalse(update_checker.is_newer("1.0.2", "1.0.2"))
        self.assertFalse(update_checker.is_newer("1.0.1", "1.0.2"))
        self.assertFalse(update_checker.is_newer("nightly", "1.0.2"))

    def test_build_version_returns_to_stable(self):
        self.assertTrue(update_checker.is_newer("1.0.2", "1.0.2+15"))
        self.assertFalse(update_checker.is_newer("1.0.2", "1.0.2"))

    def test_parse_release_selects_installer_and_digest(self):
        info = update_checker.parse_release(release_payload(), "1.0.2")
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "1.0.3")
        self.assertEqual(info.installer_name, "SAI-1.0.3-Setup.exe")
        self.assertEqual(info.installer_sha256,
                         hashlib.sha256(INSTALLER_CONTENT).hexdigest())
        self.assertTrue(info.can_install)
        self.assertIn("测试说明", info.notes)

    def test_parse_release_ignores_same_or_older(self):
        self.assertIsNone(
            update_checker.parse_release(release_payload("1.0.2"), "1.0.2"))
        self.assertIsNone(
            update_checker.parse_release(release_payload("1.0.1"), "1.0.2"))

    def test_parse_release_ignores_draft_and_prerelease(self):
        self.assertIsNone(update_checker.parse_release(
            release_payload(prerelease=True), "1.0.2"))
        self.assertIsNone(update_checker.parse_release(
            release_payload(draft=True), "1.0.2"))

    def test_fetch_latest_release_uses_session_and_proxy(self):
        session = FakeSession(FakeResponse(payload=release_payload()))
        info = update_checker.fetch_latest_release(
            "1.0.2", session=session, api_url="https://example.test/latest")
        self.assertEqual(info.version, "1.0.3")
        self.assertTrue(session.trust_env)
        self.assertEqual(session.urls, ["https://example.test/latest"])

    def test_download_installer_verifies_sha256(self):
        info = update_checker.parse_release(release_payload(), "1.0.2")
        session = FakeSession(FakeResponse(
            content=INSTALLER_CONTENT,
            headers={"Content-Length": str(len(INSTALLER_CONTENT))}))
        with tempfile.TemporaryDirectory() as directory:
            path = update_checker.download_installer(
                info, session=session, directory=directory)
            self.assertEqual(path.read_bytes(), INSTALLER_CONTENT)
            self.assertEqual(path.name, "SAI-1.0.3-Setup.exe")
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_download_installer_rejects_bad_digest(self):
        info = update_checker.parse_release(release_payload(), "1.0.2")
        session = FakeSession(FakeResponse(
            content=b"tampered", headers={"Content-Length": "8"}))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                update_checker.download_installer(
                    info, session=session, directory=directory)
            self.assertEqual(list(Path(directory).glob("*.part")), [])
            self.assertFalse(
                update_checker.installer_path(info, directory).exists())

    def test_download_installer_reuses_verified_file(self):
        info = update_checker.parse_release(release_payload(), "1.0.2")
        with tempfile.TemporaryDirectory() as directory:
            target = update_checker.installer_path(info, directory)
            target.write_bytes(INSTALLER_CONTENT)
            session = FakeSession(None)
            path = update_checker.download_installer(
                info, session=session, directory=directory)
            self.assertEqual(path, target)
            self.assertEqual(session.urls, [])

    def test_state_interval_and_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = update_checker.read_state(state_path)
            self.assertTrue(update_checker.should_check(state))
            update_checker.mark_checked(state, now=1000.0)
            self.assertFalse(update_checker.should_check(state, now=1060.0))
            self.assertTrue(update_checker.should_check(
                state, now=1000.0 + update_checker.CHECK_INTERVAL + 1))
            state["skipped_version"] = "1.0.3"
            update_checker.write_state(state, state_path)
            saved = update_checker.read_state(state_path)
            self.assertTrue(update_checker.is_skipped(saved, "1.0.3"))
            self.assertFalse(update_checker.is_skipped(saved, "1.0.4"))

    def test_create_install_script_waits_and_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            installer = directory / "SAI-1.0.3-Setup.exe"
            installer.write_bytes(b"x")
            app_exe = directory / "SAI.exe"
            app_exe.write_bytes(b"x")
            script = update_checker.create_install_script(
                installer, app_exe, directory / "update.log",
                directory=directory)
            text = script.read_text(encoding="mbcs", errors="replace")
            self.assertIn("start \"\" /wait", text)
            self.assertIn("/SILENT", text)
            self.assertIn("/SUPPRESSMSGBOXES", text)
            self.assertIn(str(installer), text)
            self.assertIn(str(app_exe), text)


class UpdateBadgeTests(unittest.TestCase):
    def test_badge_toggles_only_when_visibility_changes(self):
        from gui_launcher import Launcher
        launcher = Launcher.__new__(Launcher)
        launcher.update_badge = Mock()
        launcher.update_badge_visible = False
        launcher._show_update_badge(True)
        launcher.update_badge.pack.assert_called_once()
        launcher.update_badge.pack.reset_mock()
        # 状态没有变化时不重复布局，避免界面抖动
        launcher._show_update_badge(True)
        launcher.update_badge.pack.assert_not_called()
        launcher._show_update_badge(False)
        launcher.update_badge.pack_forget.assert_called_once()


if __name__ == "__main__":
    unittest.main()
