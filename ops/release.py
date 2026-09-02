"""Release automation for the WSL + Windows Git workflow.

This module is intentionally not wired into the public MCP server. It is a local
operator CLI so an agent can push, wait for the image build, and verify the
deployed MCP without exposing write actions to end users.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
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
DEFAULT_MCP_ID = "87440044842919710"
DEFAULT_OAUTH_REDIRECT_URI = (
    f"https://playmcp.kakao.com/api/v1/applied-mcps/{DEFAULT_MCP_ID}/"
    "authorize/oauth:callback"
)

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
        encoding="utf-8",
        errors="replace",
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


def status_paths(status: str) -> set[str]:
    """Parse porcelain short status into normalized repository paths."""
    paths: set[str] = set()
    for line in status.splitlines():
        if not line:
            continue
        raw = line[3:]
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        paths.add(raw.strip().strip('"').replace("\\", "/"))
    return paths


def commit_and_push(
    *,
    repo: Path,
    git_bin: Path,
    message: str,
    remote: str = DEFAULT_REMOTE,
    branch: str | None = None,
    stage_paths: Iterable[Path] | None = None,
) -> str:
    if stage_paths is None:
        run_cmd([git_bin, "add", "-A"], cwd=repo)
    else:
        paths = [str(path).replace("\\", "/") for path in stage_paths]
        if paths:
            run_cmd([git_bin, "add", "--", *paths], cwd=repo)

    staged = subprocess.run(
        [str(git_bin), "diff", "--cached", "--quiet"],
        cwd=str(repo),
        check=False,
    ).returncode
    if staged == 1:
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
    preexisting_dirty = status_paths(windows_status(dst, git_bin))
    copied = sync_tracked_tree(source_repo=src, dest_repo=dst, dest_git=git_bin)
    print(f"Synced {len(copied)} tracked files to {dst}", file=sys.stderr)
    stage_paths = [
        path for path in copied if str(path).replace("\\", "/") not in preexisting_dirty
    ]
    if preexisting_dirty:
        print(
            "Skipped pre-existing dirty Windows paths: "
            + ", ".join(sorted(preexisting_dirty)),
            file=sys.stderr,
        )
    return commit_and_push(
        repo=dst,
        git_bin=git_bin,
        message=message,
        stage_paths=stage_paths,
    )


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


def workflow_run_for_head(owner_repo: str, branch: str, head_sha: str) -> dict | None:
    query = urllib.parse.urlencode({"branch": branch, "per_page": 10})
    data = github_api(f"/repos/{owner_repo}/actions/runs?{query}")
    for run in data.get("workflow_runs", []):
        if run.get("head_sha") == head_sha:
            return run
    return None


def latest_check_run(owner_repo: str, head_sha: str) -> dict | None:
    data = github_api(f"/repos/{owner_repo}/commits/{head_sha}/check-runs")
    runs = data.get("check_runs", [])
    return runs[0] if runs else None


def wait_for_build(
    *,
    owner_repo: str = DEFAULT_OWNER_REPO,
    branch: str = "master",
    head_sha: str = "",
    timeout_sec: int = 600,
    interval_sec: int = 10,
) -> dict:
    deadline = time.monotonic() + timeout_sec
    last: dict | None = None
    while time.monotonic() < deadline:
        if head_sha:
            run = latest_check_run(owner_repo, head_sha)
            if run is None:
                run = workflow_run_for_head(owner_repo, branch, head_sha)
        else:
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_text(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> tuple[int, str, dict]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    opener = urllib.request.urlopen
    if not follow_redirects:
        opener = urllib.request.build_opener(_NoRedirect).open
    try:
        with opener(req, timeout=20) as response:
            return response.status, response.read().decode("utf-8", "replace"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)


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


def _header(headers: dict, name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def remote_oauth_smoke(
    base_url: str = DEFAULT_BASE_URL,
    redirect_uri: str = DEFAULT_OAUTH_REDIRECT_URI,
) -> dict:
    base = base_url.rstrip("/")
    client_payload = {
        "redirect_uris": [redirect_uri],
        "client_name": "codex release smoke",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    }
    status, raw, _ = http_text(
        f"{base}/register",
        method="POST",
        body=json.dumps(client_payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if status != 201:
        raise ReleaseError(f"/register returned {status}: {raw[:300]}")
    client = json.loads(raw)

    verifier = "codex-release-smoke-verifier-01234567890123456789"
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "state": "codex-release-smoke",
            "code_challenge": _pkce_s256(verifier),
            "code_challenge_method": "S256",
        }
    )
    status, raw, headers = http_text(
        f"{base}/authorize?{query}",
        headers={"Accept": "text/html,application/json"},
        follow_redirects=False,
    )
    consent_location = _header(headers, "location")
    consent_parts = urllib.parse.urlsplit(consent_location)
    if status != 302 or consent_parts.path != "/oauth/consent":
        raise ReleaseError(f"/authorize did not redirect to consent: {status} {raw[:300]}")

    status, consent_html, _ = http_text(
        urllib.parse.urljoin(base, consent_location),
        headers={"Accept": "text/html"},
    )
    if (
        status != 200
        or 'name="agree"' not in consent_html
        or ">확인</button>" not in consent_html
    ):
        raise ReleaseError(f"/oauth/consent page invalid: {status}")

    token = urllib.parse.parse_qs(consent_parts.query)["token"][0]
    consent_body = urllib.parse.urlencode(
        {"token": token, "decision": "allow", "agree": "yes"}
    ).encode("utf-8")
    status, raw, headers = http_text(
        f"{base}/oauth/consent",
        method="POST",
        body=consent_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
        },
        follow_redirects=False,
    )
    callback = _header(headers, "location")
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(callback).query).get("code", [""])[0]
    if status != 302 or not code:
        raise ReleaseError(f"consent did not issue callback code: {status} {raw[:300]}")

    token_body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client["client_id"],
            "client_secret": client.get("client_secret", ""),
            "code_verifier": verifier,
        }
    ).encode("utf-8")
    status, raw, _ = http_text(
        f"{base}/token",
        method="POST",
        body=token_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    if status != 200:
        raise ReleaseError(f"/token returned {status}: {raw[:300]}")
    access_token = json.loads(raw)["access_token"]

    unauth_call_body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "tools/call",
            "params": {"name": "help", "arguments": {}},
        }
    ).encode("utf-8")
    status, raw, headers = http_text(
        f"{base}/mcp",
        method="POST",
        body=unauth_call_body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    if status != 401 or "www-authenticate" not in {key.lower() for key in headers}:
        raise ReleaseError(f"unauthenticated tools/call returned {status}: {raw[:300]}")

    auth_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {access_token}",
    }
    tools_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8")
    status, raw, _ = http_text(
        f"{base}/mcp",
        method="POST",
        body=tools_body,
        headers=auth_headers,
    )
    if status != 200:
        raise ReleaseError(f"authenticated tools/list returned {status}: {raw[:300]}")
    tools = json.loads(raw)["result"]["tools"]
    tool_names = sorted(tool["name"] for tool in tools)

    quiz_body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "quiz",
                "arguments": {"mode": "시장", "nickname": "원격검증", "period": "1w"},
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    status, quiz_raw, _ = http_text(
        f"{base}/mcp",
        method="POST",
        body=quiz_body,
        headers=auth_headers,
    )
    if status != 200:
        raise ReleaseError(f"authenticated quiz call returned {status}: {quiz_raw[:300]}")

    return {
        "register_status": 201,
        "authorize_redirect": True,
        "consent_page": True,
        "token_status": 200,
        "unauth_call_status": 401,
        "tools_status": 200,
        "tool_names": tool_names,
        "quiz_status": 200,
        "quiz_has_chart_hint": "차트형 힌트" in quiz_raw,
        "quiz_has_leaderboard": "주간 TOP3" in quiz_raw,
        "ctx_exposed": '"ctx"' in raw or '"ctx"' in quiz_raw,
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
        head_sha=args.head_sha,
        timeout_sec=args.timeout,
        interval_sec=args.interval,
    )
    print(json.dumps({"id": run.get("id"), "url": run.get("html_url"), "head_sha": run.get("head_sha")}, ensure_ascii=False))
    return 0


def cmd_verify_remote(args: argparse.Namespace) -> int:
    print(json.dumps(verify_remote(args.base_url), ensure_ascii=False, indent=2))
    return 0


def cmd_oauth_smoke(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            remote_oauth_smoke(args.base_url, args.redirect_uri),
            ensure_ascii=False,
            indent=2,
        )
    )
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
    wait.add_argument("--head-sha", default="", help="wait for checks on this exact commit")
    wait.add_argument("--timeout", type=int, default=600)
    wait.add_argument("--interval", type=int, default=10)
    wait.set_defaults(func=cmd_wait_build)

    verify = sub.add_parser("verify-remote", help="check health and MCP auth/list behavior")
    verify.add_argument("--base-url", default=DEFAULT_BASE_URL)
    verify.set_defaults(func=cmd_verify_remote)

    oauth = sub.add_parser("oauth-smoke", help="run remote OAuth and authenticated quiz smoke")
    oauth.add_argument("--base-url", default=DEFAULT_BASE_URL)
    oauth.add_argument("--redirect-uri", default=DEFAULT_OAUTH_REDIRECT_URI)
    oauth.set_defaults(func=cmd_oauth_smoke)

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
