"""Release automation for the WSL + Windows Git workflow.

This module is intentionally not wired into the public MCP server. It is a local
operator CLI so an agent can push, wait for the image build, and verify the
deployed MCP without exposing write actions to end users.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_WINDOWS_REPO = Path(
    "/mnt/c/Users/82109/Downloads/stock-quiz-mcp/stock-quiz-mcp"
)
DEFAULT_WINDOWS_GIT = Path("/mnt/c/Program Files/Git/cmd/git.exe")
DEFAULT_REMOTE = "origin"
DEFAULT_OWNER_REPO = "han-chanhee/stock_quiz_mcp"
DEFAULT_BASE_URL = "https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io"
DEFAULT_PREVIEW_URL = "https://preview-chatgpt.kakao.com"

SENSITIVE_PATHS = {
    ".env",
    ".env.local",
    ".env.production",
}


class ReleaseError(RuntimeError):
    """Expected release failure with a concise human-readable message."""


@dataclass(frozen=True)
class CommandResult:
    args: Sequence[str]
    stdout: str
    stderr: str


def run_cmd(
    args: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> CommandResult:
    proc = subprocess.run(
        [str(arg) for arg in args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    result = CommandResult([str(arg) for arg in args], proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise ReleaseError(f"command failed ({proc.returncode}): {' '.join(result.args)}\n{detail}")
    return result


def wsl_repo_root(start: Path | None = None) -> Path:
    start = start or Path.cwd()
    result = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=start)
    return Path(result.stdout.strip())


def windows_git() -> Path:
    configured = Path(os.environ.get("WINDOWS_GIT", str(DEFAULT_WINDOWS_GIT)))
    if configured.exists():
        return configured
    fallback = shutil.which("git")
    if fallback:
        return Path(fallback)
    raise ReleaseError(f"Windows git not found: {configured}")


def windows_repo() -> Path:
    return Path(os.environ.get("WINDOWS_REPO", str(DEFAULT_WINDOWS_REPO)))


def tracked_files(repo: Path, git_bin: str | Path = "git") -> list[Path]:
    result = run_cmd([git_bin, "ls-files", "-z"], cwd=repo)
    files = [Path(part) for part in result.stdout.split("\0") if part]
    blocked = sorted(str(path) for path in files if str(path) in SENSITIVE_PATHS)
    if blocked:
        raise ReleaseError(f"sensitive files are tracked and will not be synced: {', '.join(blocked)}")
    return files


def ensure_windows_repo(repo: Path, git_bin: Path) -> None:
    if not repo.exists():
        raise ReleaseError(f"Windows repo does not exist: {repo}")
    run_cmd([git_bin, "rev-parse", "--git-dir"], cwd=repo)


def sync_tracked_tree(
    *,
    source_repo: Path,
    dest_repo: Path,
    dest_git: Path,
    remove_deleted: bool = True,
) -> list[Path]:
    ensure_windows_repo(dest_repo, dest_git)
    source_files = tracked_files(source_repo)
    source_set = {str(path).replace("\\", "/") for path in source_files}

    if remove_deleted:
        for path in tracked_files(dest_repo, dest_git):
            rel = str(path).replace("\\", "/")
            if rel not in source_set and rel not in SENSITIVE_PATHS:
                target = dest_repo / path
                if target.exists():
                    target.unlink()

    copied: list[Path] = []
    for rel in source_files:
        src = source_repo / rel
        dst = dest_repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)
    return copied


def windows_status(repo: Path, git_bin: Path) -> str:
    return run_cmd([git_bin, "status", "--short"], cwd=repo).stdout


def commit_and_push(
    *,
    repo: Path,
    git_bin: Path,
    message: str,
    remote: str = DEFAULT_REMOTE,
    branch: str | None = None,
) -> str:
    run_cmd([git_bin, "add", "-A"], cwd=repo)
    status = windows_status(repo, git_bin)
    if status.strip():
        run_cmd([git_bin, "commit", "-m", message], cwd=repo)
    else:
        print("No Windows repo changes to commit; pushing current HEAD.", file=sys.stderr)

    branch = branch or run_cmd([git_bin, "branch", "--show-current"], cwd=repo).stdout.strip()
    if not branch:
        raise ReleaseError("could not determine current branch")
    push = run_cmd([git_bin, "push", remote, branch], cwd=repo)
    return push.stdout + push.stderr


def sync_commit_push(message: str) -> str:
    src = wsl_repo_root()
    dst = windows_repo()
    git_bin = windows_git()
    copied = sync_tracked_tree(source_repo=src, dest_repo=dst, dest_git=git_bin)
    print(f"Synced {len(copied)} tracked files to {dst}", file=sys.stderr)
    return commit_and_push(repo=dst, git_bin=git_bin, message=message)


def github_api(path: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_workflow_run(owner_repo: str, branch: str) -> dict | None:
    query = urllib.parse.urlencode({"branch": branch, "per_page": 5})
    data = github_api(f"/repos/{owner_repo}/actions/runs?{query}")
    runs = data.get("workflow_runs", [])
    return runs[0] if runs else None


def wait_for_build(
    *,
    owner_repo: str = DEFAULT_OWNER_REPO,
    branch: str = "master",
    timeout_sec: int = 600,
    interval_sec: int = 10,
) -> dict:
    deadline = time.monotonic() + timeout_sec
    last: dict | None = None
    while time.monotonic() < deadline:
        run = latest_workflow_run(owner_repo, branch)
        if run:
            last = run
            status = run.get("status")
            conclusion = run.get("conclusion")
            print(f"workflow {run.get('id')}: {status}/{conclusion}", file=sys.stderr)
            if status == "completed":
                if conclusion == "success":
                    return run
                raise ReleaseError(f"GitHub Actions failed: {conclusion} ({run.get('html_url')})")
        time.sleep(interval_sec)
    raise ReleaseError(f"timed out waiting for GitHub Actions; last={last}")


def http_json(url: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict | str, dict]:
    data = None
    headers = {"Accept": "application/json, text/event-stream"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            try:
                parsed: dict | str = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            return response.status, parsed, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed, dict(exc.headers)


def verify_remote(base_url: str = DEFAULT_BASE_URL) -> dict:
    base = base_url.rstrip("/")
    health_status, health, _ = http_json(f"{base}/health")
    if health_status != 200:
        raise ReleaseError(f"/health failed with {health_status}: {health}")

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    tools_status, tools, headers = http_json(f"{base}/mcp", method="POST", body=payload)
    if tools_status not in {200, 401}:
        raise ReleaseError(f"/mcp tools/list returned {tools_status}: {tools}")

    if tools_status == 401 and "www-authenticate" not in {k.lower() for k in headers}:
        raise ReleaseError("OAuth 401 response is missing WWW-Authenticate")

    return {
        "health_status": health_status,
        "health": health,
        "tools_status": tools_status,
        "oauth_challenge": tools_status == 401,
    }


def open_url(url: str) -> None:
    if not url:
        raise ReleaseError("URL is empty")
    if Path("/mnt/c/Windows/System32/cmd.exe").exists():
        run_cmd(["/mnt/c/Windows/System32/cmd.exe", "/c", "start", "", url])
        return
    run_cmd(["xdg-open", url])


def cmd_push(args: argparse.Namespace) -> int:
    print(sync_commit_push(args.message))
    return 0


def cmd_wait_build(args: argparse.Namespace) -> int:
    run = wait_for_build(
        owner_repo=args.owner_repo,
        branch=args.branch,
        timeout_sec=args.timeout,
        interval_sec=args.interval,
    )
    print(json.dumps({"id": run.get("id"), "url": run.get("html_url")}, ensure_ascii=False))
    return 0


def cmd_verify_remote(args: argparse.Namespace) -> int:
    print(json.dumps(verify_remote(args.base_url), ensure_ascii=False, indent=2))
    return 0


def cmd_open_preview(args: argparse.Namespace) -> int:
    open_url(args.url or os.environ.get("KAKAO_PREVIEW_URL", DEFAULT_PREVIEW_URL))
    return 0


def cmd_open_redeploy(args: argparse.Namespace) -> int:
    url = args.url or os.environ.get("KAKAOCLOUD_REDEPLOY_URL", "")
    open_url(url)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stock quiz MCP release automation")
    sub = parser.add_subparsers(dest="command", required=True)

    push = sub.add_parser("push", help="sync WSL tracked files to Windows Git and push")
    push.add_argument("-m", "--message", required=True)
    push.set_defaults(func=cmd_push)

    wait = sub.add_parser("wait-build", help="wait for the latest GitHub Actions run")
    wait.add_argument("--owner-repo", default=DEFAULT_OWNER_REPO)
    wait.add_argument("--branch", default="master")
    wait.add_argument("--timeout", type=int, default=600)
    wait.add_argument("--interval", type=int, default=10)
    wait.set_defaults(func=cmd_wait_build)

    verify = sub.add_parser("verify-remote", help="check health and MCP auth/list behavior")
    verify.add_argument("--base-url", default=DEFAULT_BASE_URL)
    verify.set_defaults(func=cmd_verify_remote)

    preview = sub.add_parser("open-preview", help="open the Kakao Tools preview screen")
    preview.add_argument("--url", default="")
    preview.set_defaults(func=cmd_open_preview)

    redeploy = sub.add_parser("open-redeploy", help="open the KakaoCloud redeploy screen")
    redeploy.add_argument("--url", default="")
    redeploy.set_defaults(func=cmd_open_redeploy)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except ReleaseError as exc:
        print(f"release error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
