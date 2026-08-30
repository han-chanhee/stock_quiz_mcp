"""카카오 MCP용 자체 OAuth 인증서버 골격.

개인정보 제3자 제공 동의문 초안(실제 제출 전 사람의 검토 필요).
※ 정보 흐름 방향: 주식대결(우리 서비스) → 카카오. 사용자가 OAuth로 로그인하면
우리가 보유하게 되는 이용자 식별값과 그 값에 연결된 서비스 이용 정보(정답
기록·점수·랭킹)를 카카오에 제공해 Kakao Tools 답변에 노출시키는 흐름이다.
- 제공받는 자: (주) 카카오
- 제공 목적: 주식대결 퀴즈 정답/오답 기록, 가점·감점 기반 점수 산정,
  주간 랭킹(TOP3 및 본인 순위) 조회 및 Kakao Tools 답변 노출
- 제공 항목: 이용자 식별값(OAuth로 식별) 및 그에 연결된 점수·랭킹 정보
- 보유 기간: 서비스 탈퇴 또는 동의 철회 시까지 (연동 해제 화면에서 즉시 철회 가능)

현재는 배포 기본값이 비활성이며, 카카오 개인정보보호팀 승인 후에만 활성화한다.

Redirect URI는 카카오 개발 가이드 6장 1-a를 그대로 따른다(경로 오타 주의 —
"oauth/callback"이 아니라 "oauth:callback"):
  https://tools.kakao.com/api/v1/applied-mcps/{mcpId}/authorize/oauth:callback
  https://playmcp.kakao.com/api/v1/applied-mcps/{mcpId}/authorize/oauth:callback
실제 PlayMCP 관리 콘솔 도메인(playmcp.kakaocloud.io)에서 같은 callback을 쓰는 경우도
운영 호환성 차원에서 함께 허용한다.

동의 화면(consent_page)과 연동 해제 화면(disconnect_page)은 build_auth_routes()가
FastMCP의 custom_route로 등록한다. 카카오 요구사항(개발 가이드 6장 1-b) 두 가지를
모두 만족하기 위한 최소 구현이며, 실제 UI/문구는 카카오 검토 후 다듬어야 한다.
"""

from __future__ import annotations

import os
import re
import secrets
import json
import time
import base64
import binascii
import hashlib
import hmac
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qs, parse_qsl, unquote

import httpx
from fastmcp.server.auth.auth import TokenHandler
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RegistrationError,
    RefreshToken,
)
from mcp.server.auth.routes import cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.server.auth import AuthProvider


_REDIRECT_URI_TEMPLATES = (
    "https://tools.kakao.com/api/v1/applied-mcps/{mcp_id}/authorize/oauth:callback",
    "https://playmcp.kakao.com/api/v1/applied-mcps/{mcp_id}/authorize/oauth:callback",
    "https://playmcp.kakaocloud.io/api/v1/applied-mcps/{mcp_id}/authorize/oauth:callback",
)

# PlayMCP in KC 콘솔에서 기존 등록된 서버의 환경변수를 편집할 방법을 찾지
# 못해(OAUTH_MCP_ID를 배포 후 주입할 수 없음) mcpId를 상수로 고정한다.
# mcpId는 서버를 삭제·재생성하지 않는 한 바뀌지 않으므로 하드코딩해도 안전하다.
# 값이 바뀌면(재생성 등) 이 상수만 고쳐서 재배포하면 된다.
# 2026-08-30: PlayMCP OAuth 안내 메일 기준 최종 mcpId.
_HARDCODED_MCP_ID = "87440044842919710"
_LEGACY_MCP_IDS = ("83185073570028966", "3606")

# OAuth issuer/base URL. MCP SDK가 HTTPS를 강제하므로 실제 배포 도메인을 쓴다.
# (OAUTH_BASE_URL 환경변수로 덮어쓸 수 있음)
_DEFAULT_BASE_URL = "https://stock-quiz-mcp-kakaotools.playmcp-endpoint.kakaocloud.io"
_DEFAULT_OAUTH_SNAPSHOT_PATH = Path(__file__).parent.parent / "store" / "data" / "oauth.json"
_DEFAULT_PLAYMCP_CLIENT_SECRET = "stockquiz_3221c246c3f3f4e0b9cd0235c2699c0f772165438b273780"
_DEFAULT_PLAYMCP_BEARER_TOKEN = "stockquiz_preview_0c28879f772d4f17eb9dfa5f9b4c76de28a278720c57cdeb"
_KAKAO_AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
_KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
_KAKAO_USER_ME_URL = "https://kapi.kakao.com/v2/user/me"
_PLAYMCP_CLIENT_ID_RE = re.compile(r"^stockquiz-playmcp-[0-9]+$")
_PLAYMCP_CALLBACK_PATH_RE = re.compile(
    r"^/api/v1/applied-mcps/[0-9]+/authorize/oauth:callback$"
)
_PLAYMCP_CALLBACK_HOSTS = {
    "tools.kakao.com",
    "playmcp.kakao.com",
    "playmcp.kakaocloud.io",
}

CONSENT_TEXT = {
    "제공받는 자": "(주) 카카오",
    "제공 목적": "주식대결 퀴즈 정답/오답 기록, 가점·감점 기반 점수 산정, "
    "주간 랭킹(TOP3 및 본인 순위) 조회 및 Kakao Tools 답변 노출",
    "제공 항목": "이용자 식별값 및 그에 연결된 점수·랭킹 정보",
    "보유 및 이용 기간": "연동 해제 시 지체없이 파기",
}


@dataclass(frozen=True)
class KakaoLoginConfig:
    rest_api_key: str
    client_secret: str | None
    redirect_uri: str


class FlexibleStaticClientAuthenticator(ClientAuthenticator):
    """Static PlayMCP client는 secret post/basic 둘 다 허용한다."""

    async def authenticate_request(self, request: Request) -> OAuthClientInformationFull:
        try:
            return await super().authenticate_request(request)
        except AuthenticationError:
            form_data = await request.form()
            client_id = str(form_data.get("client_id") or "")
            basic_secret: str | None = None
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    basic_client_id, basic_secret = decoded.split(":", 1)
                    client_id = client_id or unquote(basic_client_id)
                    basic_secret = unquote(basic_secret)
                except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
                    raise AuthenticationError("Invalid Basic authentication header") from exc

            client = await self.provider.get_client(client_id) if client_id else None
            static_client_ids = getattr(self.provider, "_static_client_ids", set())
            if client is None or client_id not in static_client_ids:
                raise

            request_secret = form_data.get("client_secret")
            if not isinstance(request_secret, str):
                request_secret = basic_secret
            if not request_secret and client_id in static_client_ids:
                return client
            if not request_secret or not client.client_secret:
                raise AuthenticationError("Client secret is required")
            if not hmac.compare_digest(
                client.client_secret.encode(),
                request_secret.encode(),
            ):
                raise AuthenticationError("Invalid client_secret")
            return client


async def _normalize_token_grant_type(request: Request) -> Request:
    """카카오 콘솔 enum 대문자 grant_type을 표준 OAuth 값으로 정규화한다."""
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        return request

    body = await request.body()
    form_items = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
    changed = False
    normalized: list[tuple[str, str]] = []
    for key, value in form_items:
        if key == "grant_type" and value in {"AUTHORIZATION_CODE", "REFRESH_TOKEN"}:
            normalized.append((key, value.lower()))
            changed = True
        else:
            normalized.append((key, value))
    if not changed:
        return request

    normalized_body = urlencode(normalized).encode("utf-8")
    headers = []
    for key, value in request.scope.get("headers", []):
        if key == b"content-length":
            headers.append((key, str(len(normalized_body)).encode("ascii")))
        else:
            headers.append((key, value))
    scope = dict(request.scope)
    scope["headers"] = headers

    async def receive() -> dict[str, object]:
        return {
            "type": "http.request",
            "body": normalized_body,
            "more_body": False,
        }

    return Request(scope, receive)


class KakaoTokenHandler(TokenHandler):
    """Token endpoint wrapper for PlayMCP/Kakao console compatibility."""

    async def handle(self, request: Request):
        return await super().handle(await _normalize_token_grant_type(request))


def _is_playmcp_client_id(client_id: str) -> bool:
    return bool(_PLAYMCP_CLIENT_ID_RE.fullmatch(client_id))


def _is_trusted_playmcp_redirect_uri(uri: str) -> bool:
    parts = urlsplit(uri)
    return (
        parts.scheme == "https"
        and parts.netloc in _PLAYMCP_CALLBACK_HOSTS
        and bool(_PLAYMCP_CALLBACK_PATH_RE.fullmatch(parts.path))
        and not parts.fragment
    )


def _copy_client_with_redirects(
    client: OAuthClientInformationFull, redirect_uris: list[str]
) -> OAuthClientInformationFull:
    data = client.model_dump(mode="json")
    data["redirect_uris"] = redirect_uris
    return OAuthClientInformationFull.model_validate(data)


class PlayMCPAuthorizationHandler(AuthorizationHandler):
    """새로 발급되는 PlayMCP callback URI를 authorize 직전에 client에 반영한다."""

    async def handle(self, request: Request):
        params = request.query_params if request.method == "GET" else await request.form()
        client_id = str(params.get("client_id") or "")
        redirect_uri = str(params.get("redirect_uri") or "")
        if client_id and _is_trusted_playmcp_redirect_uri(redirect_uri):
            await self.provider.ensure_redirect_uri_for_client(client_id, redirect_uri)
        return await super().handle(request)


class KakaoRestrictedOAuthProvider(InMemoryOAuthProvider):
    """카카오가 사전 등록한 Redirect URI만 허용하고, 명시적 동의를 거친 뒤에만
    인가 코드를 발급하는 OAuth 프로바이더.

    InMemoryOAuthProvider.authorize()는 동의 절차 없이 즉시 인가 코드를 발급한다
    (테스트용 스텁이라 당연함). 카카오 개발 가이드 6장 1-b가 "개인정보 제3자 제공
    동의를 받는 화면"을 요구하므로, authorize() 앞단에 동의 여부 확인을 추가한다.
    """

    def __init__(
        self,
        allowed_redirect_uris: tuple[str, ...],
        snapshot_path: Path | None = None,
        kakao_login_config: KakaoLoginConfig | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.allowed_redirect_uris = allowed_redirect_uris
        self._snapshot_path = snapshot_path or _DEFAULT_OAUTH_SNAPSHOT_PATH
        self.kakao_login_config = kakao_login_config
        self._state_secret = (
            os.environ.get("OAUTH_STATE_SECRET", "").strip()
            or os.environ.get("OAUTH_PLAYMCP_CLIENT_SECRET", "").strip()
            or _DEFAULT_PLAYMCP_CLIENT_SECRET
        ).encode("utf-8")
        self._allowed_redirect_uri_set = set(allowed_redirect_uris)
        self._consented_clients: set[str] = set()
        self._static_client_ids: set[str] = set()
        # 동의 대기 중인 요청: consent_token -> (client, params, user_subject)
        self._pending_consents: dict[
            str, tuple[OAuthClientInformationFull, AuthorizationParams, str]
        ] = {}
        self._pending_kakao_logins: dict[
            str, tuple[OAuthClientInformationFull, AuthorizationParams]
        ] = {}
        self._oauth_events: list[dict[str, object]] = []

    def _record_oauth_event(self, event: str, **details: object) -> None:
        scrubbed = {
            key: value
            for key, value in details.items()
            if key not in {"code", "token", "access_token", "refresh_token", "secret"}
        }
        self._oauth_events.append(
            {"at": int(time.time()), "event": event, **scrubbed}
        )
        self._oauth_events = self._oauth_events[-20:]

    def install_static_client(self, client_info: OAuthClientInformationFull) -> None:
        """콘솔 필수 입력형 OAuth client를 부팅 시 항상 등록한다."""
        client_id = client_info.client_id or ""
        if not client_id:
            raise ValueError("static client_id is required")
        self.clients[client_id] = client_info
        self._static_client_ids.add(client_id)

    def install_static_bearer_token(self, token: str, client_id: str) -> None:
        """PlayMCP 직접 입력 인증헤더 검사에 쓰는 고정 Bearer token을 등록한다."""
        if not token or not client_id:
            raise ValueError("static bearer token and client_id are required")
        self.access_tokens[token] = AccessToken(
            token=token,
            client_id=client_id,
            scopes=[],
            expires_at=None,
        )

    def _static_client_for_id(self, client_id: str) -> OAuthClientInformationFull | None:
        if not _is_playmcp_client_id(client_id):
            return None
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=(
                os.environ.get("OAUTH_PLAYMCP_CLIENT_SECRET", "").strip()
                or _DEFAULT_PLAYMCP_CLIENT_SECRET
            ),
            client_secret_expires_at=None,
            redirect_uris=list(self.allowed_redirect_uris),
            token_endpoint_auth_method="client_secret_post",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            client_name="주식대결 PlayMCP",
        )

    async def ensure_redirect_uri_for_client(
        self, client_id: str, redirect_uri: str
    ) -> None:
        if not _is_trusted_playmcp_redirect_uri(redirect_uri):
            return
        client = await self.get_client(client_id)
        if client is None:
            return
        registered = [str(uri) for uri in client.redirect_uris or []]
        merged_redirects = list(dict.fromkeys([*registered, redirect_uri]))
        if merged_redirects != registered:
            self.clients[client_id] = _copy_client_with_redirects(
                client, merged_redirects
            )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """저장된 DCR client도 현재 운영 callback allowlist를 따라가게 보정한다."""
        client = await super().get_client(client_id)
        if client is None:
            client = self._static_client_for_id(client_id)
            if client is None:
                return None
            self.install_static_client(client)
        registered = [str(uri) for uri in client.redirect_uris or []]
        merged_redirects = list(dict.fromkeys([*registered, *self.allowed_redirect_uris]))
        if merged_redirects == registered:
            return client
        updated = _copy_client_with_redirects(client, merged_redirects)
        self.clients[client_id] = updated
        return updated

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        """요청된 모든 Redirect URI가 화이트리스트에 있을 때만 등록한다."""
        for uri in client_info.redirect_uris or ():
            uri_text = str(uri)
            if (
                uri_text not in self._allowed_redirect_uri_set
                and not _is_trusted_playmcp_redirect_uri(uri_text)
            ):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    f"허용되지 않은 redirect_uri: {uri_text}",
                )

        await super().register_client(client_info)
        self.snapshot_save()

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        result: list[Route] = []
        for route in routes:
            if (
                isinstance(route, Route)
                and route.path == "/authorize"
                and route.methods is not None
                and ("GET" in route.methods or "POST" in route.methods)
            ):
                result.append(
                    Route(
                        path="/authorize",
                        endpoint=PlayMCPAuthorizationHandler(self).handle,
                        methods=["GET", "POST"],
                    )
                )
            elif (
                isinstance(route, Route)
                and route.path == "/token"
                and route.methods is not None
                and "POST" in route.methods
            ):
                token_handler = KakaoTokenHandler(
                    provider=self,
                    client_authenticator=FlexibleStaticClientAuthenticator(self),
                )
                result.append(
                    Route(
                        path="/token",
                        endpoint=cors_middleware(
                            token_handler.handle, ["POST", "OPTIONS"]
                        ),
                        methods=["POST", "OPTIONS"],
                    )
                )
            elif (
                isinstance(route, Route)
                and route.path == "/revoke"
                and route.methods is not None
                and "POST" in route.methods
            ):
                revocation_handler = RevocationHandler(
                    provider=self,
                    client_authenticator=FlexibleStaticClientAuthenticator(self),
                )
                result.append(
                    Route(
                        path="/revoke",
                        endpoint=cors_middleware(
                            revocation_handler.handle, ["POST", "OPTIONS"]
                        ),
                        methods=["POST", "OPTIONS"],
                    )
                )
            else:
                result.append(route)
        return result

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        subject = authorization_code.subject
        self._record_oauth_event(
            "token_exchange_started",
            client_id=client.client_id,
            subject_present=bool(subject),
        )
        token = await super().exchange_authorization_code(client, authorization_code)
        if subject:
            access_token = self.access_tokens.get(token.access_token)
            if access_token is not None:
                access_token.subject = subject
            if token.refresh_token:
                refresh_token = self.refresh_tokens.get(token.refresh_token)
                if refresh_token is not None:
                    refresh_token.subject = subject
        self.snapshot_save()
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        subject = refresh_token.subject
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        if subject:
            access_token = self.access_tokens.get(token.access_token)
            if access_token is not None:
                access_token.subject = subject
            if token.refresh_token:
                new_refresh = self.refresh_tokens.get(token.refresh_token)
                if new_refresh is not None:
                    new_refresh.subject = subject
        self.snapshot_save()
        return token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await super().revoke_token(token)
        self.snapshot_save()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """이미 동의한 클라이언트만 실제 인가를 진행한다.

        동의 전이면 인가 코드를 발급하지 않고, /oauth/consent 화면으로 안내하는
        redirect URL을 대신 반환한다(authorize()의 반환 계약이 "리다이렉트할 URL
        문자열"이므로 그대로 재사용). 사용자가 동의 화면에서 승인하면
        _finish_authorize()가 실제 authorize()를 이어서 호출한다.
        """
        if self.kakao_login_config is not None:
            self._record_oauth_event("authorize_started", client_id=client.client_id)
            login_state = self._encode_kakao_state(client, params)
            self._pending_kakao_logins[login_state] = (client, params)
            return self._kakao_authorize_redirect(login_state)
        return self.begin_local_consent(client, params)

    def begin_local_consent(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
        subject: str | None = None,
    ) -> str:
        user_subject = subject or f"playmcp-user-{secrets.token_urlsafe(18)}"
        consent_token = self._encode_consent_token(client, params, user_subject)
        self._pending_consents[consent_token] = (client, params, user_subject)
        return f"/oauth/consent?token={consent_token}"

    def _kakao_authorize_redirect(self, login_state: str) -> str:
        config = self.kakao_login_config
        if config is None:
            raise RuntimeError("Kakao Login is not configured")
        return _KAKAO_AUTHORIZE_URL + "?" + urlencode(
            {
                "response_type": "code",
                "client_id": config.rest_api_key,
                "redirect_uri": config.redirect_uri,
                "state": login_state,
            }
        )

    async def finish_kakao_login(self, login_state: str, code: str) -> str:
        pending = self._pending_kakao_logins.pop(login_state, None)
        if pending is None:
            pending = await self._decode_kakao_state(login_state)
        access_token = await self._exchange_kakao_code(code)
        subject = await self._fetch_kakao_subject(access_token)
        client, params = pending
        return self.begin_local_consent(client, params, subject)

    def _encode_kakao_state(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        payload = {
            "v": 1,
            "iat": int(time.time()),
            "nonce": secrets.token_urlsafe(8),
            "client_id": client.client_id or "",
            "redirect_uri": str(params.redirect_uri),
            "state": params.state,
            "scopes": params.scopes or [],
            "code_challenge": params.code_challenge,
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
        }
        encoded = _base64url_json(payload)
        signature = hmac.new(
            self._state_secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        return f"{encoded}.{encoded_signature}"

    async def _decode_kakao_state(
        self, login_state: str
    ) -> tuple[OAuthClientInformationFull, AuthorizationParams]:
        try:
            encoded, encoded_signature = login_state.split(".", 1)
        except ValueError as exc:
            raise ValueError("카카오 로그인 state 형식이 올바르지 않습니다.") from exc
        expected = hmac.new(
            self._state_secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        padding = "=" * (-len(encoded_signature) % 4)
        try:
            actual = base64.urlsafe_b64decode(
                (encoded_signature + padding).encode("ascii")
            )
        except (binascii.Error, ValueError) as exc:
            raise ValueError("카카오 로그인 state 서명이 올바르지 않습니다.") from exc
        if not hmac.compare_digest(expected, actual):
            raise ValueError("카카오 로그인 state 서명이 일치하지 않습니다.")

        payload = _unbase64url_json(encoded)
        issued_at = int(payload.get("iat", 0))
        if issued_at < int(time.time()) - 600:
            raise ValueError("카카오 로그인 state가 만료되었습니다.")
        client_id = str(payload.get("client_id") or "")
        redirect_uri = str(payload.get("redirect_uri") or "")
        code_challenge = str(payload.get("code_challenge") or "")
        if not client_id or not redirect_uri or not code_challenge:
            raise ValueError("카카오 로그인 state에 필수 값이 없습니다.")
        client = await self.get_client(client_id)
        if client is None:
            raise ValueError("카카오 로그인 state의 OAuth client를 찾을 수 없습니다.")
        registered = [str(uri) for uri in client.redirect_uris or []]
        if redirect_uri not in registered and not _is_trusted_playmcp_redirect_uri(
            redirect_uri
        ):
            raise ValueError("카카오 로그인 state의 redirect_uri가 허용되지 않았습니다.")
        raw_scopes = payload.get("scopes")
        scopes = raw_scopes if isinstance(raw_scopes, list) else []
        params = AuthorizationParams(
            state=str(payload.get("state") or ""),
            scopes=[str(scope) for scope in scopes],
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=bool(
                payload.get("redirect_uri_provided_explicitly", True)
            ),
        )
        return client, params

    def _encode_consent_token(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
        subject: str,
    ) -> str:
        payload = {
            "v": 1,
            "iat": int(time.time()),
            "nonce": secrets.token_urlsafe(8),
            "client_id": client.client_id or "",
            "redirect_uri": str(params.redirect_uri),
            "state": params.state,
            "scopes": params.scopes or [],
            "code_challenge": params.code_challenge,
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "subject": subject,
        }
        encoded = _base64url_json(payload)
        signature = hmac.new(
            self._state_secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        return f"{encoded}.{encoded_signature}"

    async def _decode_consent_token(
        self, consent_token: str
    ) -> tuple[OAuthClientInformationFull, AuthorizationParams, str]:
        client, params, payload = await self._decode_signed_oauth_payload(consent_token)
        subject = str(payload.get("subject") or "")
        if not subject:
            raise ValueError("동의 token에 사용자 식별값이 없습니다.")
        return client, params, subject

    def _encode_authorization_code(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
        subject: str,
    ) -> str:
        payload = {
            "v": 1,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": secrets.token_urlsafe(8),
            "client_id": client.client_id or "",
            "redirect_uri": str(params.redirect_uri),
            "state": params.state,
            "scopes": params.scopes or [],
            "code_challenge": params.code_challenge,
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "subject": subject,
        }
        encoded = _base64url_json(payload)
        signature = hmac.new(
            self._state_secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        return f"sqc.{encoded}.{encoded_signature}"

    async def _decode_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode:
        if not authorization_code.startswith("sqc."):
            raise ValueError("인가 code 형식이 올바르지 않습니다.")
        signed_value = authorization_code[4:]
        recovered_client, params, payload = await self._decode_signed_oauth_payload(
            signed_value
        )
        if recovered_client.client_id != client.client_id:
            raise ValueError("인가 code의 client_id가 일치하지 않습니다.")
        expires_at = float(payload.get("exp", 0))
        if expires_at < time.time():
            raise ValueError("인가 code가 만료되었습니다.")
        subject = str(payload.get("subject") or "")
        return AuthorizationCode(
            code=authorization_code,
            scopes=params.scopes or [],
            expires_at=expires_at,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            subject=subject or None,
        )

    async def _decode_signed_oauth_payload(
        self, value: str
    ) -> tuple[OAuthClientInformationFull, AuthorizationParams, dict[str, object]]:
        try:
            encoded, encoded_signature = value.split(".", 1)
        except ValueError as exc:
            raise ValueError("서명 token 형식이 올바르지 않습니다.") from exc
        expected = hmac.new(
            self._state_secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        padding = "=" * (-len(encoded_signature) % 4)
        try:
            actual = base64.urlsafe_b64decode(
                (encoded_signature + padding).encode("ascii")
            )
        except (binascii.Error, ValueError) as exc:
            raise ValueError("서명 token의 서명이 올바르지 않습니다.") from exc
        if not hmac.compare_digest(expected, actual):
            raise ValueError("서명 token의 서명이 일치하지 않습니다.")

        payload = _unbase64url_json(encoded)
        issued_at = int(payload.get("iat", 0))
        if issued_at < int(time.time()) - 1800:
            raise ValueError("서명 token이 만료되었습니다.")
        client_id = str(payload.get("client_id") or "")
        redirect_uri = str(payload.get("redirect_uri") or "")
        code_challenge = str(payload.get("code_challenge") or "")
        if not client_id or not redirect_uri or not code_challenge:
            raise ValueError("서명 token에 필수 값이 없습니다.")
        client = await self.get_client(client_id)
        if client is None:
            raise ValueError("서명 token의 OAuth client를 찾을 수 없습니다.")
        registered = [str(uri) for uri in client.redirect_uris or []]
        if redirect_uri not in registered and not _is_trusted_playmcp_redirect_uri(
            redirect_uri
        ):
            raise ValueError("서명 token의 redirect_uri가 허용되지 않았습니다.")
        raw_scopes = payload.get("scopes")
        scopes = raw_scopes if isinstance(raw_scopes, list) else []
        params = AuthorizationParams(
            state=str(payload.get("state") or ""),
            scopes=[str(scope) for scope in scopes],
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=bool(
                payload.get("redirect_uri_provided_explicitly", True)
            ),
        )
        return client, params, payload

    async def _exchange_kakao_code(self, code: str) -> str:
        config = self.kakao_login_config
        if config is None:
            raise RuntimeError("Kakao Login is not configured")
        data = {
            "grant_type": "authorization_code",
            "client_id": config.rest_api_key,
            "redirect_uri": config.redirect_uri,
            "code": code,
        }
        if config.client_secret:
            data["client_secret"] = config.client_secret
        response = await self._post_kakao_token(data)
        if response.status_code == 401 and config.client_secret:
            retry_data = dict(data)
            retry_data.pop("client_secret", None)
            response = await self._post_kakao_token(retry_data)
        response.raise_for_status()
        payload = response.json()
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("카카오 token 응답에 access_token이 없습니다.")
        return access_token

    async def _post_kakao_token(self, data: dict[str, str]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(_KAKAO_TOKEN_URL, data=data)

    async def _fetch_kakao_subject(self, access_token: str) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                _KAKAO_USER_ME_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        response.raise_for_status()
        payload = response.json()
        kakao_user_id = payload.get("id")
        if kakao_user_id is None:
            raise ValueError("카카오 사용자 응답에 id가 없습니다.")
        return f"kakao:{kakao_user_id}"

    async def finish_authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
        subject: str,
    ) -> str:
        """동의 완료 후 짧은 opaque 인가 코드를 발급하고 subject를 연결한다."""
        code = f"test_auth_code_{secrets.token_hex(16)}"
        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 300,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            subject=subject,
        )
        self.auth_codes[code] = auth_code
        self._record_oauth_event(
            "auth_code_issued",
            client_id=client.client_id,
            subject_present=bool(subject),
            code_length=len(code),
        )
        self.snapshot_save()
        return _with_query(str(params.redirect_uri), {"code": code, "state": params.state})

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        self.snapshot_load()
        auth_code = await super().load_authorization_code(client, authorization_code)
        if auth_code is not None:
            self._record_oauth_event(
                "auth_code_loaded_from_memory",
                client_id=client.client_id,
            )
            return auth_code
        if not authorization_code.startswith("sqc."):
            self._record_oauth_event(
                "auth_code_missing",
                client_id=client.client_id,
            )
            return None
        try:
            auth_code = await self._decode_authorization_code(
                client, authorization_code
            )
        except ValueError as exc:
            self._record_oauth_event(
                "auth_code_restore_failed",
                client_id=client.client_id,
                reason=str(exc)[:120],
            )
            return None
        self.auth_codes[authorization_code] = auth_code
        self._record_oauth_event(
            "auth_code_restored",
            client_id=client.client_id,
            subject_present=bool(auth_code.subject),
        )
        return auth_code

    async def revoke_client_consent(self, client_id: str) -> None:
        """연동 해제: 동의 기록과 발급된 토큰/등록 정보를 모두 지운다."""
        self._consented_clients.discard(client_id)
        if client_id not in self._static_client_ids:
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
        self._access_to_refresh_map = {
            access: refresh
            for access, refresh in self._access_to_refresh_map.items()
            if access in self.access_tokens and refresh in self.refresh_tokens
        }
        self._refresh_to_access_map = {
            refresh: access
            for refresh, access in self._refresh_to_access_map.items()
            if refresh in self.refresh_tokens and access in self.access_tokens
        }
        self.snapshot_save()

    def _quarantine_snapshot(self) -> None:
        if not self._snapshot_path.exists():
            return
        target = self._snapshot_path.with_suffix(
            self._snapshot_path.suffix + f".corrupt.{int(time.time())}"
        )
        self._snapshot_path.replace(target)

    def snapshot_save(self) -> None:
        """DCR clients, active tokens, token pair maps, and consent flags are persisted."""
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "clients": {
                client_id: client.model_dump(mode="json")
                for client_id, client in self.clients.items()
            },
            "access_tokens": {
                token: info.model_dump(mode="json")
                for token, info in self.access_tokens.items()
            },
            "refresh_tokens": {
                token: info.model_dump(mode="json")
                for token, info in self.refresh_tokens.items()
            },
            "access_to_refresh": dict(self._access_to_refresh_map),
            "refresh_to_access": dict(self._refresh_to_access_map),
            "consented_clients": sorted(self._consented_clients),
            "auth_codes": {
                code: info.model_dump(mode="json")
                for code, info in self.auth_codes.items()
                if info.expires_at >= time.time()
            },
        }
        tmp = self._snapshot_path.with_suffix(self._snapshot_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._snapshot_path)

    def snapshot_load(self) -> None:
        """Restore persisted OAuth runtime state if a snapshot exists."""
        if not self._snapshot_path.exists():
            return
        try:
            payload = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            self.clients = {
                client_id: OAuthClientInformationFull.model_validate(client)
                for client_id, client in payload.get("clients", {}).items()
            }
            self.access_tokens = {
                token: AccessToken.model_validate(info)
                for token, info in payload.get("access_tokens", {}).items()
            }
            self.refresh_tokens = {
                token: RefreshToken.model_validate(info)
                for token, info in payload.get("refresh_tokens", {}).items()
            }
            self.auth_codes = {
                str(code): AuthorizationCode.model_validate(info)
                for code, info in payload.get("auth_codes", {}).items()
                if AuthorizationCode.model_validate(info).expires_at >= time.time()
            }
            self._consented_clients = set(payload.get("consented_clients", []))
            self._access_to_refresh_map = {
                access: refresh
                for access, refresh in payload.get("access_to_refresh", {}).items()
                if access in self.access_tokens and refresh in self.refresh_tokens
            }
            self._refresh_to_access_map = {
                refresh: access
                for refresh, access in payload.get("refresh_to_access", {}).items()
                if refresh in self.refresh_tokens and access in self.access_tokens
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            self._quarantine_snapshot()


def _static_playmcp_client(
    *, mcp_id: str, allowed_redirect_uris: tuple[str, ...]
) -> OAuthClientInformationFull:
    client_id = (
        os.environ.get("OAUTH_PLAYMCP_CLIENT_ID", "").strip()
        or f"stockquiz-playmcp-{mcp_id}"
    )
    client_secret = (
        os.environ.get("OAUTH_PLAYMCP_CLIENT_SECRET", "").strip()
        or _DEFAULT_PLAYMCP_CLIENT_SECRET
    )
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret=client_secret,
        client_secret_expires_at=None,
        redirect_uris=list(allowed_redirect_uris),
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        client_name="주식대결 PlayMCP",
    )


def _playmcp_mcp_ids() -> tuple[str, ...]:
    primary = os.environ.get("OAUTH_MCP_ID", "").strip() or _HARDCODED_MCP_ID
    extra = tuple(
        item.strip()
        for item in os.environ.get("OAUTH_EXTRA_MCP_IDS", "").split(",")
        if item.strip()
    )
    ordered = (primary, *_LEGACY_MCP_IDS, *extra)
    return tuple(dict.fromkeys(ordered))


def _static_playmcp_bearer_token() -> str:
    return (
        os.environ.get("OAUTH_PLAYMCP_BEARER_TOKEN", "").strip()
        or _DEFAULT_PLAYMCP_BEARER_TOKEN
    )


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _kakao_login_config(base_url: str) -> KakaoLoginConfig | None:
    rest_api_key = _first_env("KAKAO_REST_API_KEY", "KAKAO_LOGIN_REST_API_KEY")
    if not rest_api_key:
        return None
    configured_redirect_uri = _first_env(
        "KAKAO_REDIRECT_URI", "KAKAO_LOGIN_REDIRECT_URI"
    )
    redirect_uri = configured_redirect_uri or f"{base_url.rstrip('/')}/oauth/kakao/callback"
    client_secret = _first_env("KAKAO_CLIENT_SECRET", "KAKAO_LOGIN_CLIENT_SECRET") or None
    return KakaoLoginConfig(
        rest_api_key=rest_api_key,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )


def _with_query(url: str, values: dict[str, str | None]) -> str:
    """기존 query를 보존하면서 OAuth callback 파라미터를 덧붙인다."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({key: value for key, value in values.items() if value is not None})
    return urlunsplit(parts._replace(query=urlencode(query)))


def _authorization_error_redirect(
    params: AuthorizationParams,
    error: str,
    description: str = "authorization failed",
) -> str:
    """동의 거부도 OAuth callback으로 돌려보내 linking UI가 닫히게 한다."""
    return _with_query(
        str(params.redirect_uri),
        {
            "error": error,
            "error_description": description,
            "state": params.state,
        },
    )


def _base64url_json(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unbase64url_json(value: str) -> dict[str, object]:
    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode((value + padding).encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("state payload must be an object")
    return payload


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
    notice = f"<p style='color:green'>{escape(message)}</p>" if message else ""
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


def _oauth_runtime_diagnostics(provider: KakaoRestrictedOAuthProvider) -> dict[str, object]:
    config = provider.kakao_login_config
    rest_key = config.rest_api_key if config else ""
    return {
        "oauth_enabled": True,
        "external_login_enabled": config is not None,
        "external_key_present": bool(rest_key),
        "external_key_suffix": rest_key[-6:] if rest_key else None,
        "external_secret_present": bool(config and config.client_secret),
        "external_redirect_uri": config.redirect_uri if config else None,
        "recent_events": provider._oauth_events[-10:],
    }


def register_auth_routes(mcp: "FastMCP", provider: KakaoRestrictedOAuthProvider) -> None:
    """동의 화면(/oauth/consent)과 연동 해제 화면(/oauth/disconnect)을 등록한다.

    카카오 개발 가이드 6장 1-b 두 요건(동의 화면, 연동 해제 화면)을 만족시키기
    위한 최소 구현. OAUTH_ENABLED=1일 때만 main.py에서 호출된다.
    """

    @mcp.custom_route("/oauth/consent", methods=["GET"])
    async def consent_get(request: Request) -> HTMLResponse:
        token = request.query_params.get("token", "")
        if token not in provider._pending_consents:
            try:
                await provider._decode_consent_token(token)
            except ValueError as exc:
                provider._record_oauth_event(
                    "consent_token_invalid",
                    reason=str(exc)[:120],
                )
                return HTMLResponse(
                    f"만료되었거나 잘못된 동의 요청입니다: {escape(str(exc))}",
                    status_code=400,
                )
        provider._record_oauth_event("consent_page_shown")
        return HTMLResponse(_consent_page_html(token))

    @mcp.custom_route("/oauth/consent", methods=["POST"])
    async def consent_post(request: Request):
        form = await request.form()
        token = str(form.get("token", ""))
        decision = str(form.get("decision", ""))
        pending = provider._pending_consents.pop(token, None)
        if pending is None:
            try:
                pending = await provider._decode_consent_token(token)
            except ValueError as exc:
                provider._record_oauth_event(
                    "consent_token_restore_failed",
                    reason=str(exc)[:120],
                )
                return HTMLResponse(
                    f"만료되었거나 잘못된 동의 요청입니다: {escape(str(exc))}",
                    status_code=400,
                )
            provider._record_oauth_event("consent_token_restored")

        client, params, subject = pending
        if decision != "allow":
            provider._record_oauth_event("consent_denied", client_id=client.client_id)
            return RedirectResponse(
                _authorization_error_redirect(params, "access_denied"),
                status_code=302,
            )

        client_id = client.client_id or ""
        provider._consented_clients.add(client_id)
        provider.snapshot_save()
        redirect_uri = await provider.finish_authorize(client, params, subject)
        provider._record_oauth_event("consent_allowed", client_id=client_id)
        return RedirectResponse(redirect_uri, status_code=302)

    @mcp.custom_route("/oauth/kakao/callback", methods=["GET"])
    async def kakao_callback_get(request: Request):
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        error = request.query_params.get("error", "")
        provider._record_oauth_event(
            "kakao_callback_received",
            code_present=bool(code),
            error_present=bool(error),
        )
        pending = provider._pending_kakao_logins.get(state)
        if pending is None:
            try:
                _, params = await provider._decode_kakao_state(state)
            except ValueError as exc:
                provider._record_oauth_event(
                    "kakao_state_restore_failed",
                    reason=str(exc)[:120],
                )
                return HTMLResponse(
                    f"만료되었거나 잘못된 카카오 로그인 요청입니다: {escape(str(exc))}",
                    status_code=400,
                )
            provider._record_oauth_event("kakao_state_restored")
        else:
            _, params = pending
        if error:
            provider._pending_kakao_logins.pop(state, None)
            return RedirectResponse(
                _authorization_error_redirect(
                    params,
                    "access_denied",
                    "user denied or Kakao Login returned an authorization error",
                ),
                status_code=302,
            )
        if not code:
            provider._pending_kakao_logins.pop(state, None)
            return HTMLResponse("카카오 콜백에 code가 없습니다.", status_code=400)
        try:
            consent_url = await provider.finish_kakao_login(state, code)
        except httpx.HTTPStatusError as exc:
            provider._record_oauth_event(
                "kakao_token_exchange_failed",
                status_code=exc.response.status_code,
                body=exc.response.text[:200],
            )
            return HTMLResponse(
                f"카카오 토큰 교환이 실패했습니다. HTTP {exc.response.status_code}: "
                f"{escape(exc.response.text[:500])}",
                status_code=502,
            )
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            provider._record_oauth_event(
                "kakao_login_failed",
                reason=str(exc)[:120],
            )
            return RedirectResponse(
                _authorization_error_redirect(params, "server_error", str(exc)[:200]),
                status_code=302,
            )
        provider._record_oauth_event("kakao_login_finished")
        return RedirectResponse(consent_url, status_code=302)

    @mcp.custom_route("/oauth/runtime", methods=["GET"])
    async def oauth_runtime_get(request: Request) -> JSONResponse:
        return JSONResponse(_oauth_runtime_diagnostics(provider))

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


def register_oauth_protocol_routes(
    mcp: "FastMCP",
    provider: KakaoRestrictedOAuthProvider,
    *,
    mcp_path: str = "/mcp",
) -> None:
    """OAuth protocol routes만 등록하고 MCP transport 자체는 공개로 둔다."""
    mcp._additional_http_routes.extend(provider.get_routes(mcp_path=mcp_path))


def build_auth_provider() -> "AuthProvider | None":
    """OAUTH_ENABLED=1 환경변수가 설정된 경우에만 인증 프로바이더를 구성해 반환한다.
    미설정 시 None(비활성 — 지금 배포 기본값).

    mcpId는 OAUTH_MCP_ID 환경변수가 있으면 그 값을, 없으면 _HARDCODED_MCP_ID를
    쓴다(콘솔에서 환경변수를 나중에 주입할 수 있게 되면 그쪽이 우선한다)."""
    if os.environ.get("OAUTH_ENABLED") != "1":
        return None

    mcp_ids = _playmcp_mcp_ids()
    primary_mcp_id = mcp_ids[0]

    allowed_redirect_uris = tuple(
        template.format(mcp_id=mcp_id)
        for mcp_id in mcp_ids
        for template in _REDIRECT_URI_TEMPLATES
    )
    # base_url을 명시하지 않으면 InMemoryOAuthProvider 기본값(http://fastmcp.example.com)이
    # 쓰이는데, MCP SDK가 issuer URL에 HTTPS를 강제해 기동 시 ValueError로 죽는다
    # (2026-08-19 배포 실패 실측: "Issuer URL must be HTTPS").
    base_url = os.environ.get("OAUTH_BASE_URL", "").strip() or _DEFAULT_BASE_URL
    # Dynamic Client Registration(/register)을 켠다. MCP 인증 스펙(2025-03-26)이
    # "MCP clients and servers SHOULD support RFC7591"이라고 권고하며, 이게 없으면
    # 클라이언트가 client_id를 얻을 방법이 없어 인증 흐름이 시작조차 되지 않는다
    # (401 -> 메타데이터 조회 -> /register -> /authorize -> /token 순서).
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=allowed_redirect_uris,
        snapshot_path=Path(
            os.environ.get("OAUTH_SNAPSHOT_PATH", "").strip()
            or _DEFAULT_OAUTH_SNAPSHOT_PATH
        ),
        kakao_login_config=_kakao_login_config(base_url),
        base_url=base_url,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )
    provider.snapshot_load()
    static_clients = [
        _static_playmcp_client(
            mcp_id=primary_mcp_id,
            allowed_redirect_uris=allowed_redirect_uris,
        )
    ]
    if "OAUTH_PLAYMCP_CLIENT_ID" not in os.environ:
        static_clients.extend(
            _static_playmcp_client(
                mcp_id=mcp_id,
                allowed_redirect_uris=allowed_redirect_uris,
            )
            for mcp_id in mcp_ids[1:]
        )
    for static_client in static_clients:
        provider.install_static_client(static_client)
    provider.install_static_bearer_token(
        _static_playmcp_bearer_token(),
        static_clients[0].client_id or "",
    )
    return provider
