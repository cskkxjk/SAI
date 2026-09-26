"""用 Release 资产的 sha256 摘要重建 SHA256SUMS.txt。

发布流水线在各自上传完产物后调用本脚本：文件内容始终由 Release 当前资产推导，
与调用顺序无关（最后运行的那次写出同样的完整文件），不会再出现某个平台覆盖
其它平台校验和条目的问题。

用法：python .github/scripts/update_sha256sums.py v1.0.6
环境变量：GH_TOKEN 或 GITHUB_TOKEN（需要 contents: write 权限），GITHUB_REPOSITORY=owner/repo
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
SUM_NAME = "SHA256SUMS.txt"


def request(url: str, method: str = "GET", data: bytes | None = None,
            content_type: str = "") -> dict | None:
    headers = {
        "Authorization": "Bearer " + (
            os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
        ),
        "Accept": "application/vnd.github+json",
        "User-Agent": "sai-release-checksums",
    }
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "cskkxjk/SAI")
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_REF_NAME", "")
    if not tag:
        print("缺少 tag 参数")
        return 1
    try:
        release = request(f"{API}/repos/{repo}/releases/tags/{tag}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"Release {tag} 尚不存在，跳过校验和更新")
            return 0
        raise
    lines = []
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        digest = asset.get("digest") or ""
        if name == SUM_NAME:
            continue
        if not digest.startswith("sha256:"):
            print(f"警告：{name} 没有 sha256 摘要，已跳过")
            continue
        lines.append(f"{digest.removeprefix('sha256:')}  {name}")
    if not lines:
        print("Release 上没有可用于校验和的资产，跳过更新")
        return 0
    content = "\n".join(sorted(lines)) + "\n"
    checksum = hashlib.sha256(content.encode()).hexdigest()
    existing = next(
        (a for a in release.get("assets", []) if a.get("name") == SUM_NAME), None
    )
    if existing and (existing.get("digest") or "") == f"sha256:{checksum}":
        print(f"{SUM_NAME} 已是最新（{len(lines)} 条）")
        return 0
    if existing:
        request(f"{API}/repos/{repo}/releases/assets/{existing['id']}", method="DELETE")
    uploaded = request(
        f"https://uploads.github.com/repos/{repo}/releases/{release['id']}"
        f"/assets?name={SUM_NAME}",
        method="POST",
        data=content.encode(),
        content_type="text/plain; charset=utf-8",
    )
    print(f"已更新 {SUM_NAME}（{len(lines)} 条），内容 sha256:{checksum}")
    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
