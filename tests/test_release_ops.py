"""Local release automation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops import release


def _git(repo: Path, *args: str) -> None:
    release.run_cmd(["git", *args], cwd=repo)


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def test_sync_tracked_tree_copies_tracked_files_and_removes_deleted(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _init_repo(src)
    _init_repo(dst)

    (src / "server").mkdir()
    (src / "server" / "main.py").write_text("print('new')\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "src")

    (dst / "old.py").write_text("old\n", encoding="utf-8")
    _git(dst, "add", "-A")
    _git(dst, "commit", "-m", "dst")

    copied = release.sync_tracked_tree(
        source_repo=src,
        dest_repo=dst,
        dest_git=Path("git"),
    )

    assert copied == [Path("server/main.py")]
    assert (dst / "server" / "main.py").read_text(encoding="utf-8") == "print('new')\n"
    assert not (dst / "old.py").exists()


def test_tracked_sensitive_files_are_rejected(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "bad")

    with pytest.raises(release.ReleaseError, match="sensitive files"):
        release.tracked_files(repo)


def test_verify_remote_accepts_oauth_challenge(monkeypatch):
    calls = []

    def fake_http_json(url: str, *, method: str = "GET", body: dict | None = None):
        calls.append((url, method, body))
        if url.endswith("/health"):
            return 200, {"status": "ok"}, {}
        return 401, "unauthorized", {"WWW-Authenticate": "Bearer resource_metadata=x"}

    monkeypatch.setattr(release, "http_json", fake_http_json)

    result = release.verify_remote("https://example.test/")

    assert result["oauth_challenge"] is True
    assert calls[1][2] == {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def test_cli_verify_remote_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(
        release,
        "verify_remote",
        lambda base_url: {"health_status": 200, "base": base_url},
    )

    assert release.main(["verify-remote", "--base-url", "https://example.test"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "health_status": 200,
        "base": "https://example.test",
    }
