"""카카오 MCP용 자체 OAuth 인증서버 골격.

개인정보 제3자 제공 동의문 초안(실제 제출 전 사람의 검토 필요):
- 제공받는 자: 주식대결 서비스 운영자
- 제공 목적: 퀴즈 이용자 식별 및 랭킹 제공
- 제공 항목: 카카오가 승인한 이용자 식별자
- 보유 기간: 서비스 탈퇴 또는 동의 철회 시까지

현재는 배포 기본값이 비활성이며, 카카오 개인정보보호팀 승인 후에만 활성화한다.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.shared.auth import OAuthClientInformationFull

if TYPE_CHECKING:
    from fastmcp.server.auth import AuthProvider


_REDIRECT_URI_TEMPLATES = (
    "https://tools.kakao.com/api/v1/applied-mcps/{mcp_id}/oauth/callback",
    "https://playmcp.kakao.com/api/v1/applied-mcps/{mcp_id}/oauth/callback",
)


class KakaoRestrictedOAuthProvider(InMemoryOAuthProvider):
    """카카오가 사전 등록한 Redirect URI만 허용하는 OAuth 프로바이더."""

    def __init__(self, allowed_redirect_uris: tuple[str, ...], **kwargs) -> None:
        super().__init__(**kwargs)
        self.allowed_redirect_uris = allowed_redirect_uris
        self._allowed_redirect_uri_set = set(allowed_redirect_uris)

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
