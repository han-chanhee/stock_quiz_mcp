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
        "https://tools.kakao.com/api/v1/applied-mcps/test-id/oauth/callback",
        "https://playmcp.kakao.com/api/v1/applied-mcps/test-id/oauth/callback",
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
