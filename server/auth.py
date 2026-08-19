"""카카오 MCP용 자체 OAuth 인증서버 골격.

개인정보 제3자 제공 동의문 초안(실제 제출 전 사람의 검토 필요):
- 제공받는 자: 주식대결 서비스 운영자
- 제공 목적: 퀴즈 이용자 식별 및 랭킹 제공
- 제공 항목: 카카오가 승인한 이용자 식별자
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
from typing import TYPE_CHECKING

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import AuthorizationParams
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

CONSENT_TEXT = {
    "제공받는 자": "(주) 카카오",
    "제공 목적": "주식대결 서비스 제공을 위한 Kakao Tools 연동 및 관리, 서비스 호출 및 응답 처리, "
    "서비스 품질 향상 및 개선, 고객 문의 대응",
    "제공 항목": "Kakao Tools 연동을 위한 인증 정보(이용자 식별자)",
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


def _consent_page_html(consent_token: str) -> str:
    rows = "".join(
        f"<tr><th>{key}</th><td>{value}</td></tr>"
        for key, value in CONSENT_TEXT.items()
    )
    return f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>주식대결 - 개인정보 제3자 제공 동의</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:40px auto;">
  <h2>Kakao Tools 연동 동의</h2>
  <p>주식대결 서비스 이용을 위해 아래 정보가 카카오로 제공됩니다.</p>
  <table border="1" cellpadding="8" style="border-collapse:collapse;width:100%;">
    {rows}
  </table>
  <form method="post" action="/oauth/consent">
    <input type="hidden" name="token" value="{consent_token}">
    <button type="submit" name="decision" value="allow" style="margin-top:16px;padding:8px 16px;">동의하고 계속하기</button>
    <button type="submit" name="decision" value="deny" style="margin-top:16px;padding:8px 16px;">거부</button>
  </form>
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
            return HTMLResponse("동의를 거부해 연동이 취소되었습니다.")

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
    미설정 시 None(비활성 — 지금 배포 기본값)."""
    if os.environ.get("OAUTH_ENABLED") != "1":
        return None

    mcp_id = os.environ.get("OAUTH_MCP_ID", "").strip()
    if not mcp_id:
        return None

    allowed_redirect_uris = tuple(
        template.format(mcp_id=mcp_id) for template in _REDIRECT_URI_TEMPLATES
    )
    return KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=allowed_redirect_uris
    )
