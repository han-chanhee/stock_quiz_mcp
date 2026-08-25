"""카카오 MCP용 자체 OAuth 인증서버 골격.

개인정보 제3자 제공 동의문 초안(실제 제출 전 사람의 검토 필요).
※ 정보 흐름 방향: 주식대결(우리 서비스) → 카카오. 사용자가 OAuth로 로그인하면
우리가 보유하게 되는 이용자 식별값과 그 값에 연결된 서비스 이용 정보(정답
기록·점수·랭킹)를 카카오에 제공해 Kakao Tools 답변에 노출시키는 흐름이다.
- 제공받는 자: (주) 카카오
- 제공 목적: 주식대결 퀴즈 정답/오답 기록, 시도 횟수 기반 점수 산정,
  주간 랭킹(TOP5 및 본인 순위) 조회 및 Kakao Tools 답변 노출
- 제공 항목: 이용자 식별값(OAuth로 식별) 및 그에 연결된 점수·랭킹 정보
- 보유 기간: 서비스 탈퇴 또는 동의 철회 시까지 (연동 해제 화면에서 즉시 철회 가능)

현재는 배포 기본값이 비활성이며, 카카오 개인정보보호팀 승인 후에만 활성화한다.

Redirect URI는 카카오 개발 가이드 6장 1-a를 그대로 따른다(경로 오타 주의 —
"oauth/callback"이 아니라 "oauth:callback"):
  https://tools.kakao.com/api/v1/applied-mcps/{mcpId}/authorize/oauth:callback
  https://playmcp.kakao.com/api/v1/applied-mcps/{mcpId}/authorize/oauth:callback

동의 화면(consent_page)과 연동 해제 화면(disconnect_page)은 build_auth_routes()가
FastMCP의 custom_route로 등록한다. 카카오 요구사항(개발 가이드 6장 1-b) 두 가지를
모두 만족하기 위한 최소 구현이며, 실제 UI/문구는 카카오 검토 후 다듬어야 한다.
"""

from __future__ import annotations

import os
import secrets
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import AuthorizationParams
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.server.auth import AuthProvider


_REDIRECT_URI_TEMPLATES = (
    "https://tools.kakao.com/api/v1/applied-mcps/{mcp_id}/authorize/oauth:callback",
    "https://playmcp.kakao.com/api/v1/applied-mcps/{mcp_id}/authorize/oauth:callback",
)

# PlayMCP in KC 콘솔에서 기존 등록된 서버의 환경변수를 편집할 방법을 찾지
# 못해(OAUTH_MCP_ID를 배포 후 주입할 수 없음) mcpId를 상수로 고정한다.
# mcpId는 서버를 삭제·재생성하지 않는 한 바뀌지 않으므로 하드코딩해도 안전하다.
# 값이 바뀌면(재생성 등) 이 상수만 고쳐서 재배포하면 된다.
# 2026-08-19: Git 소스 방식으로 서버 재생성, mcpId가 3556 -> 3606으로 변경됨.
_HARDCODED_MCP_ID = "3606"

# OAuth issuer/base URL. MCP SDK가 HTTPS를 강제하므로 실제 배포 도메인을 쓴다.
# (OAUTH_BASE_URL 환경변수로 덮어쓸 수 있음)
_DEFAULT_BASE_URL = "https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io"

CONSENT_TEXT = {
    "제공받는 자": "(주) 카카오",
    "제공 목적": "주식대결 퀴즈 정답/오답 기록, 시도 횟수 기반 점수 산정, "
    "주간 랭킹(TOP5 및 본인 순위) 조회 및 Kakao Tools 답변 노출",
    "제공 항목": "이용자 식별값 및 그에 연결된 점수·랭킹 정보",
    "보유 및 이용 기간": "연동 해제 시 지체없이 파기",
}


class KakaoRestrictedOAuthProvider(InMemoryOAuthProvider):
    """카카오가 사전 등록한 Redirect URI만 허용하고, 명시적 동의를 거친 뒤에만
    인가 코드를 발급하는 OAuth 프로바이더.

    InMemoryOAuthProvider.authorize()는 동의 절차 없이 즉시 인가 코드를 발급한다
    (테스트용 스텁이라 당연함). 카카오 개발 가이드 6장 1-b가 "개인정보 제3자 제공
    동의를 받는 화면"을 요구하므로, authorize() 앞단에 동의 여부 확인을 추가한다.
    """

    def __init__(self, allowed_redirect_uris: tuple[str, ...], **kwargs) -> None:
        super().__init__(**kwargs)
        self.allowed_redirect_uris = allowed_redirect_uris
        self._allowed_redirect_uri_set = set(allowed_redirect_uris)
        # 동의를 마친 (client_id) 집합. 프로세스 재시작 시 초기화됨 —
        # 토큰/클라이언트와 동일하게 영속화는 별도 태스크 범위.
        self._consented_clients: set[str] = set()
        # 동의 대기 중인 요청: consent_token -> (client, params)
        self._pending_consents: dict[
            str, tuple[OAuthClientInformationFull, AuthorizationParams]
        ] = {}

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        """요청된 모든 Redirect URI가 화이트리스트에 있을 때만 등록한다."""
        for uri in client_info.redirect_uris or ():
            uri_text = str(uri)
            if uri_text not in self._allowed_redirect_uri_set:
                raise ValueError(f"허용되지 않은 redirect_uri: {uri_text}")

        # 토큰과 클라이언트 영속화는 별도 태스크에서 구현한다.
        await super().register_client(client_info)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """이미 동의한 클라이언트만 실제 인가를 진행한다.

        동의 전이면 인가 코드를 발급하지 않고, /oauth/consent 화면으로 안내하는
        redirect URL을 대신 반환한다(authorize()의 반환 계약이 "리다이렉트할 URL
        문자열"이므로 그대로 재사용). 사용자가 동의 화면에서 승인하면
        _finish_authorize()가 실제 authorize()를 이어서 호출한다.
        """
        client_id = client.client_id or ""
        if client_id in self._consented_clients:
            return await super().authorize(client, params)

        consent_token = secrets.token_urlsafe(16)
        self._pending_consents[consent_token] = (client, params)
        return f"/oauth/consent?token={consent_token}"

    async def revoke_client_consent(self, client_id: str) -> None:
        """연동 해제: 동의 기록과 발급된 토큰/등록 정보를 모두 지운다."""
        self._consented_clients.discard(client_id)
        self.clients.pop(client_id, None)
        stale_access = [
            token
            for token, info in self.access_tokens.items()
            if info.client_id == client_id
        ]
        for token in stale_access:
            self.access_tokens.pop(token, None)
        stale_refresh = [
            token
            for token, info in self.refresh_tokens.items()
            if info.client_id == client_id
        ]
        for token in stale_refresh:
            self.refresh_tokens.pop(token, None)


def _with_query(url: str, values: dict[str, str | None]) -> str:
    """기존 query를 보존하면서 OAuth callback 파라미터를 덧붙인다."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({key: value for key, value in values.items() if value is not None})
    return urlunsplit(parts._replace(query=urlencode(query)))


def _authorization_error_redirect(params: AuthorizationParams, error: str) -> str:
    """동의 거부도 OAuth callback으로 돌려보내 linking UI가 닫히게 한다."""
    return _with_query(
        str(params.redirect_uri),
        {
            "error": error,
            "error_description": "user denied third-party information sharing consent",
            "state": params.state,
        },
    )


def _consent_page_html(consent_token: str) -> str:
    rows = "".join(
        f"<tr><th scope=\"row\">{escape(key)}</th><td>{escape(value)}</td></tr>"
        for key, value in CONSENT_TEXT.items()
    )
    token = escape(consent_token, quote=True)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>주식대결 - 개인정보 제3자 제공 동의</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f6f7f9;
      color: #17191c;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(100%, 520px);
      padding: 28px 20px;
      background: #fff;
      border: 1px solid #e4e7ec;
      border-radius: 12px;
      box-shadow: 0 12px 36px rgba(17, 24, 39, .08);
    }}
    h1 {{ margin: 0 0 10px; font-size: 22px; line-height: 1.3; }}
    p {{ margin: 0 0 18px; color: #4b5563; line-height: 1.55; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
    th, td {{ border: 1px solid #d9dee7; padding: 11px 12px; vertical-align: top; }}
    th {{ width: 32%; background: #f9fafb; text-align: left; color: #303846; }}
    .notice {{ font-size: 13px; color: #596579; }}
    .actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 18px; }}
    button {{
      min-height: 44px;
      border: 1px solid #cfd6e3;
      border-radius: 8px;
      background: #fff;
      color: #20252d;
      font-weight: 700;
      cursor: pointer;
    }}
    button[value="allow"] {{ background: #111827; border-color: #111827; color: #fff; }}
    @media (max-width: 420px) {{
      body {{ display: block; background: #fff; }}
      main {{ min-height: 100vh; border: 0; border-radius: 0; box-shadow: none; }}
      .actions {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>주식대결 연동 동의</h1>
  <p>퀴즈 점수와 랭킹을 표시하기 위해 아래 정보 제공에 동의해주세요.</p>
  <table aria-label="개인정보 제3자 제공 항목">
    {rows}
  </table>
  <p class="notice">동의하지 않으면 연동은 취소됩니다. 연동 해제 화면에서 언제든 동의를 철회할 수 있습니다.</p>
  <form method="post" action="/oauth/consent">
    <input type="hidden" name="token" value="{token}">
    <div class="actions">
      <button type="submit" name="decision" value="allow">동의하고 계속</button>
      <button type="submit" name="decision" value="deny">거부</button>
    </div>
  </form>
</main>
</body>
</html>"""


def _disconnect_page_html(message: str | None = None) -> str:
    notice = f"<p style='color:green'>{message}</p>" if message else ""
    return f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>주식대결 - Kakao Tools 연동 해제</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:40px auto;">
  <h2>Kakao Tools 연동 해제</h2>
  <p>연동을 해제하면 카카오에 전달된 인증 정보가 즉시 파기됩니다.</p>
  {notice}
  <form method="post" action="/oauth/disconnect">
    <input type="text" name="client_id" placeholder="연동 시 발급된 client_id" required
      style="width:100%;padding:8px;margin-bottom:8px;">
    <button type="submit" style="padding:8px 16px;">연동 해제</button>
  </form>
</body>
</html>"""


def register_auth_routes(mcp: "FastMCP", provider: KakaoRestrictedOAuthProvider) -> None:
    """동의 화면(/oauth/consent)과 연동 해제 화면(/oauth/disconnect)을 등록한다.

    카카오 개발 가이드 6장 1-b 두 요건(동의 화면, 연동 해제 화면)을 만족시키기
    위한 최소 구현. OAUTH_ENABLED=1일 때만 main.py에서 호출된다.
    """

    @mcp.custom_route("/oauth/consent", methods=["GET"])
    async def consent_get(request: Request) -> HTMLResponse:
        token = request.query_params.get("token", "")
        if token not in provider._pending_consents:
            return HTMLResponse("만료되었거나 잘못된 동의 요청입니다.", status_code=400)
        return HTMLResponse(_consent_page_html(token))

    @mcp.custom_route("/oauth/consent", methods=["POST"])
    async def consent_post(request: Request):
        form = await request.form()
        token = str(form.get("token", ""))
        decision = str(form.get("decision", ""))
        pending = provider._pending_consents.pop(token, None)
        if pending is None:
            return HTMLResponse("만료되었거나 잘못된 동의 요청입니다.", status_code=400)

        client, params = pending
        if decision != "allow":
            return RedirectResponse(
                _authorization_error_redirect(params, "access_denied"),
                status_code=302,
            )

        client_id = client.client_id or ""
        provider._consented_clients.add(client_id)
        redirect_uri = await provider.authorize(client, params)
        return RedirectResponse(redirect_uri, status_code=302)

    @mcp.custom_route("/oauth/disconnect", methods=["GET"])
    async def disconnect_get(request: Request) -> HTMLResponse:
        return HTMLResponse(_disconnect_page_html())

    @mcp.custom_route("/oauth/disconnect", methods=["POST"])
    async def disconnect_post(request: Request) -> HTMLResponse:
        form = await request.form()
        client_id = str(form.get("client_id", "")).strip()
        if not client_id:
            return HTMLResponse(_disconnect_page_html("client_id를 입력해주세요."))
        await provider.revoke_client_consent(client_id)
        return HTMLResponse(_disconnect_page_html("연동이 해제되었습니다."))


def build_auth_provider() -> "AuthProvider | None":
    """OAUTH_ENABLED=1 환경변수가 설정된 경우에만 인증 프로바이더를 구성해 반환한다.
    미설정 시 None(비활성 — 지금 배포 기본값).

    mcpId는 OAUTH_MCP_ID 환경변수가 있으면 그 값을, 없으면 _HARDCODED_MCP_ID를
    쓴다(콘솔에서 환경변수를 나중에 주입할 수 있게 되면 그쪽이 우선한다)."""
    if os.environ.get("OAUTH_ENABLED") != "1":
        return None

    mcp_id = os.environ.get("OAUTH_MCP_ID", "").strip() or _HARDCODED_MCP_ID

    allowed_redirect_uris = tuple(
        template.format(mcp_id=mcp_id) for template in _REDIRECT_URI_TEMPLATES
    )
    # base_url을 명시하지 않으면 InMemoryOAuthProvider 기본값(http://fastmcp.example.com)이
    # 쓰이는데, MCP SDK가 issuer URL에 HTTPS를 강제해 기동 시 ValueError로 죽는다
    # (2026-08-19 배포 실패 실측: "Issuer URL must be HTTPS").
    base_url = os.environ.get("OAUTH_BASE_URL", "").strip() or _DEFAULT_BASE_URL
    # Dynamic Client Registration(/register)을 켠다. MCP 인증 스펙(2025-03-26)이
    # "MCP clients and servers SHOULD support RFC7591"이라고 권고하며, 이게 없으면
    # 클라이언트가 client_id를 얻을 방법이 없어 인증 흐름이 시작조차 되지 않는다
    # (401 -> 메타데이터 조회 -> /register -> /authorize -> /token 순서).
    return KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=allowed_redirect_uris,
        base_url=base_url,
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
