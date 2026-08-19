"""기본 비활성 OAuth 인증서버 골격 테스트."""

import pytest
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.shared.auth import OAuthClientInformationFull

from server.auth import KakaoRestrictedOAuthProvider, build_auth_provider


def test_oauth_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OAUTH_ENABLED", raising=False)
    monkeypatch.delenv("OAUTH_MCP_ID", raising=False)

    assert build_auth_provider() is None


def test_oauth_requires_mcp_id(monkeypatch):
    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.delenv("OAUTH_MCP_ID", raising=False)

    assert build_auth_provider() is None


def test_oauth_provider_uses_injected_mcp_id(monkeypatch):
    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.setenv("OAUTH_MCP_ID", "test-id")

    provider = build_auth_provider()

    assert isinstance(provider, InMemoryOAuthProvider)
    assert provider.allowed_redirect_uris == (
        "https://tools.kakao.com/api/v1/applied-mcps/test-id/authorize/oauth:callback",
        "https://playmcp.kakao.com/api/v1/applied-mcps/test-id/authorize/oauth:callback",
    )


@pytest.mark.asyncio
async def test_oauth_rejects_unregistered_redirect_uri():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="rejected-client",
        redirect_uris=["https://attacker.example/oauth/callback"],
    )

    with pytest.raises(
        ValueError,
        match="허용되지 않은 redirect_uri: https://attacker.example/oauth/callback",
    ):
        await provider.register_client(client)


@pytest.mark.asyncio
async def test_oauth_registers_allowed_redirect_uri():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="allowed-client",
        redirect_uris=["https://allowed.example/oauth/callback"],
    )

    await provider.register_client(client)

    assert await provider.get_client("allowed-client") == client


def _params():
    from mcp.server.auth.provider import AuthorizationParams

    return AuthorizationParams(
        state="s1",
        scopes=None,
        code_challenge="challenge",
        redirect_uri="https://allowed.example/oauth/callback",
        redirect_uri_provided_explicitly=True,
    )


@pytest.mark.asyncio
async def test_authorize_without_consent_redirects_to_consent_page():
    """카카오 개발 가이드 6장 1-b: 동의 화면 없이 바로 인가 코드를 내주면 안 된다."""
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="c1", redirect_uris=["https://allowed.example/oauth/callback"]
    )
    await provider.register_client(client)

    result = await provider.authorize(client, _params())

    assert result.startswith("/oauth/consent?token=")
    assert len(provider._pending_consents) == 1


@pytest.mark.asyncio
async def test_authorize_after_consent_issues_real_redirect():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="c2", redirect_uris=["https://allowed.example/oauth/callback"]
    )
    await provider.register_client(client)
    provider._consented_clients.add("c2")

    result = await provider.authorize(client, _params())

    assert result.startswith("https://allowed.example/oauth/callback")
    assert "code=" in result


@pytest.mark.asyncio
async def test_revoke_client_consent_clears_tokens_and_consent():
    """연동 해제: 동의 기록과 발급된 토큰이 모두 삭제된다."""
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="c3", redirect_uris=["https://allowed.example/oauth/callback"]
    )
    await provider.register_client(client)
    provider._consented_clients.add("c3")
    provider.access_tokens["tok1"] = type(
        "T", (), {"client_id": "c3"}
    )()  # 최소 스텁, client_id 속성만 필요

    await provider.revoke_client_consent("c3")

    assert "c3" not in provider._consented_clients
    assert "c3" not in provider.clients
    assert "tok1" not in provider.access_tokens
