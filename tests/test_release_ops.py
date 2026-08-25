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


def test_status_paths_parses_renames_and_normal_paths():
    status = " M server/auth.py\nR  old.py -> ops/release.py\n?? tests/test_release_ops.py\n"

    assert release.status_paths(status) == {
        "server/auth.py",
        "ops/release.py",
        "tests/test_release_ops.py",
    }


def test_commit_and_push_stages_only_requested_paths(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    release.run_cmd(["git", "init", "--bare", remote])
    _init_repo(work)
    _git(work, "remote", "add", "origin", str(remote))
    (work / "keep.txt").write_text("base\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "origin", "master")

    (work / "keep.txt").write_text("dirty\n", encoding="utf-8")
    (work / "ship.txt").write_text("ship\n", encoding="utf-8")

    release.commit_and_push(
        repo=work,
        git_bin=Path("git"),
        message="ship only",
        stage_paths=[Path("ship.txt")],
    )

    assert " M keep.txt" in release.windows_status(work, Path("git"))
    log = release.run_cmd(["git", "log", "--oneline", "-n", "1"], cwd=work).stdout
    assert "ship only" in log


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


def test_wait_for_build_can_target_head_sha(monkeypatch):
    seen = []

    def fake_latest_check_run(owner_repo: str, head_sha: str):
        seen.append((owner_repo, head_sha))
        return {
            "id": 7,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://example.test/run",
        }

    monkeypatch.setattr(release, "latest_check_run", fake_latest_check_run)

    run = release.wait_for_build(
        owner_repo="owner/repo",
        branch="main",
        head_sha="abc123",
        timeout_sec=1,
        interval_sec=0,
    )

    assert run["id"] == 7
    assert seen == [("owner/repo", "abc123")]
