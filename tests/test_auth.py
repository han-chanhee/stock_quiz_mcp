"""기본 비활성 OAuth 인증서버 골격 테스트."""

import pytest
from urllib.parse import parse_qs, urlsplit
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import AccessToken
from mcp.shared.auth import OAuthClientInformationFull

from server.auth import (
    KakaoRestrictedOAuthProvider,
    _authorization_error_redirect,
    _consent_page_html,
    _disconnect_page_html,
    build_auth_provider,
)


def test_oauth_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OAUTH_ENABLED", raising=False)
    monkeypatch.delenv("OAUTH_MCP_ID", raising=False)

    assert build_auth_provider() is None


def test_oauth_falls_back_to_hardcoded_mcp_id_when_env_missing(monkeypatch):
    """PlayMCP 콘솔에서 기존 서버에 환경변수를 추가할 방법을 못 찾아, mcpId는
    OAUTH_MCP_ID 환경변수가 없으면 server.auth._HARDCODED_MCP_ID로 폴백한다."""
    from server.auth import _HARDCODED_MCP_ID

    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.delenv("OAUTH_MCP_ID", raising=False)

    provider = build_auth_provider()

    assert provider is not None
    assert all(f"/{_HARDCODED_MCP_ID}/" in uri for uri in provider.allowed_redirect_uris)


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


def test_oauth_provider_loads_configured_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "oauth.json"
    original = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        snapshot_path=path,
    )
    original.clients["c-load"] = OAuthClientInformationFull(
        client_id="c-load",
        redirect_uris=["https://allowed.example/oauth/callback"],
    )
    original._consented_clients.add("c-load")
    original.snapshot_save()

    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.setenv("OAUTH_MCP_ID", "test-id")
    monkeypatch.setenv("OAUTH_SNAPSHOT_PATH", str(path))

    restored = build_auth_provider()

    assert restored is not None
    assert "c-load" in restored.clients
    assert "c-load" in restored._consented_clients


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
async def test_oauth_snapshot_round_trip_after_token_issue(tmp_path):
    path = tmp_path / "oauth.json"
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        snapshot_path=path,
    )
    client = OAuthClientInformationFull(
        client_id="c-token", redirect_uris=["https://allowed.example/oauth/callback"]
    )
    await provider.register_client(client)
    provider._consented_clients.add("c-token")
    redirect = await provider.authorize(client, _params())
    code = parse_qs(urlsplit(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)

    token = await provider.exchange_authorization_code(client, auth_code)

    restored = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        snapshot_path=path,
    )
    restored.snapshot_load()

    assert await restored.get_client("c-token") == client
    assert await restored.load_access_token(token.access_token) is not None
    assert await restored.load_refresh_token(client, token.refresh_token) is not None
    assert "c-token" in restored._consented_clients


@pytest.mark.asyncio
async def test_revoke_token_updates_oauth_snapshot(tmp_path):
    path = tmp_path / "oauth.json"
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        snapshot_path=path,
    )
    access = AccessToken(token="tok", client_id="c1", scopes=[])
    provider.access_tokens["tok"] = access
    provider.snapshot_save()

    await provider.revoke_token(access)

    restored = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        snapshot_path=path,
    )
    restored.snapshot_load()
    assert await restored.load_access_token("tok") is None


def test_consent_page_escapes_token_and_has_mobile_viewport():
    html = _consent_page_html('tok"><script>alert(1)</script>')

    assert '<meta name="viewport"' in html
    assert 'value="tok&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in html
    assert "<script>alert(1)</script>" not in html
    assert "동의하고 계속" in html


def test_disconnect_page_escapes_message():
    html = _disconnect_page_html("<script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_authorization_error_redirect_preserves_state_and_query():
    params = _params()
    params.redirect_uri = "https://allowed.example/oauth/callback?existing=1"

    redirect = _authorization_error_redirect(params, "access_denied")

    assert redirect.startswith("https://allowed.example/oauth/callback?")
    assert "existing=1" in redirect
    assert "error=access_denied" in redirect
    assert "state=s1" in redirect


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
