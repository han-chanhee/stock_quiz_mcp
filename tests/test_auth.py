"""기본 비활성 OAuth 인증서버 골격 테스트."""

import pytest
from urllib.parse import parse_qs, urlsplit
import httpx
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import AccessToken, RegistrationError
from mcp.shared.auth import OAuthClientInformationFull

from server.auth import (
    KakaoLoginConfig,
    KakaoRestrictedOAuthProvider,
    _authorization_error_redirect,
    _consent_page_html,
    _disconnect_page_html,
    _DEFAULT_PLAYMCP_BEARER_TOKEN,
    _DEFAULT_PLAYMCP_CLIENT_SECRET,
    _oauth_runtime_diagnostics,
    _kakao_login_config,
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
    assert any(f"/{_HARDCODED_MCP_ID}/" in uri for uri in provider.allowed_redirect_uris)
    assert f"stockquiz-playmcp-{_HARDCODED_MCP_ID}" in provider.clients


def test_oauth_provider_uses_injected_mcp_id(monkeypatch):
    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.setenv("OAUTH_MCP_ID", "test-id")

    provider = build_auth_provider()

    assert isinstance(provider, InMemoryOAuthProvider)
    assert provider.allowed_redirect_uris == (
        "https://tools.kakao.com/api/v1/applied-mcps/test-id/authorize/oauth:callback",
        "https://playmcp.kakao.com/api/v1/applied-mcps/test-id/authorize/oauth:callback",
        "https://playmcp.kakaocloud.io/api/v1/applied-mcps/test-id/authorize/oauth:callback",
        "https://tools.kakao.com/api/v1/applied-mcps/83185073570028966/authorize/oauth:callback",
        "https://playmcp.kakao.com/api/v1/applied-mcps/83185073570028966/authorize/oauth:callback",
        "https://playmcp.kakaocloud.io/api/v1/applied-mcps/83185073570028966/authorize/oauth:callback",
        "https://tools.kakao.com/api/v1/applied-mcps/3606/authorize/oauth:callback",
        "https://playmcp.kakao.com/api/v1/applied-mcps/3606/authorize/oauth:callback",
        "https://playmcp.kakaocloud.io/api/v1/applied-mcps/3606/authorize/oauth:callback",
    )
    static = provider.clients["stockquiz-playmcp-test-id"]
    assert static.client_secret == _DEFAULT_PLAYMCP_CLIENT_SECRET
    assert static.token_endpoint_auth_method == "client_secret_post"
    assert static.grant_types == ["authorization_code", "refresh_token"]
    assert static.response_types == ["code"]
    static_token = provider.access_tokens[_DEFAULT_PLAYMCP_BEARER_TOKEN]
    assert static_token.client_id == "stockquiz-playmcp-test-id"
    assert static_token.expires_at is None


def test_oauth_static_client_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.setenv("OAUTH_MCP_ID", "test-id")
    monkeypatch.setenv("OAUTH_PLAYMCP_CLIENT_ID", "custom-client")
    monkeypatch.setenv("OAUTH_PLAYMCP_CLIENT_SECRET", "custom-secret")
    monkeypatch.setenv("OAUTH_PLAYMCP_BEARER_TOKEN", "custom-bearer")

    provider = build_auth_provider()

    static = provider.clients["custom-client"]
    assert static.client_secret == "custom-secret"
    assert provider.access_tokens["custom-bearer"].client_id == "custom-client"
    assert "stockquiz-playmcp-test-id" not in provider.clients


@pytest.mark.asyncio
async def test_oauth_rejects_unregistered_redirect_uri():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="rejected-client",
        redirect_uris=["https://attacker.example/oauth/callback"],
    )

    with pytest.raises(RegistrationError) as exc_info:
        await provider.register_client(client)
    assert exc_info.value.error == "invalid_redirect_uri"
    assert (
        exc_info.value.error_description
        == "허용되지 않은 redirect_uri: https://attacker.example/oauth/callback"
    )


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


@pytest.mark.asyncio
async def test_oauth_get_client_refreshes_cached_redirect_allowlist():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=(
            "https://old.example/oauth/callback",
            "https://new.example/oauth/callback",
        )
    )
    provider.clients["cached-client"] = OAuthClientInformationFull(
        client_id="cached-client",
        redirect_uris=["https://old.example/oauth/callback"],
    )

    client = await provider.get_client("cached-client")

    assert client is not None
    assert [str(uri) for uri in client.redirect_uris] == [
        "https://old.example/oauth/callback",
        "https://new.example/oauth/callback",
    ]
    assert provider.clients["cached-client"].redirect_uris == client.redirect_uris


@pytest.mark.asyncio
async def test_oauth_accepts_future_playmcp_redirect_and_client_id():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    redirect_uri = (
        "https://playmcp.kakao.com/api/v1/applied-mcps/"
        "99999999999999999/authorize/oauth:callback"
    )
    client = OAuthClientInformationFull(
        client_id="future-client",
        redirect_uris=[redirect_uri],
    )

    await provider.register_client(client)
    static = await provider.get_client("stockquiz-playmcp-99999999999999999")

    registered = await provider.get_client("future-client")
    assert registered is not None
    assert redirect_uri in [str(uri) for uri in registered.redirect_uris]
    assert static is not None
    assert static.client_secret == _DEFAULT_PLAYMCP_CLIENT_SECRET


@pytest.mark.asyncio
async def test_oauth_rejects_future_untrusted_redirect():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="bad-future",
        redirect_uris=[
            "https://attacker.example/api/v1/applied-mcps/"
            "99999999999999999/authorize/oauth:callback"
        ],
    )

    with pytest.raises(RegistrationError):
        await provider.register_client(client)


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


def test_kakao_login_config_uses_issuer_callback(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "rest-api-key")
    monkeypatch.delenv("KAKAO_REDIRECT_URI", raising=False)
    monkeypatch.delenv("KAKAO_CLIENT_SECRET", raising=False)

    config = _kakao_login_config("https://issuer.example/")

    assert config == KakaoLoginConfig(
        rest_api_key="rest-api-key",
        client_secret=None,
        redirect_uri="https://issuer.example/oauth/kakao/callback",
    )


@pytest.mark.asyncio
async def test_authorize_with_kakao_login_redirects_to_kakao_authorize():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        kakao_login_config=KakaoLoginConfig(
            rest_api_key="rest-api-key",
            client_secret=None,
            redirect_uri="https://issuer.example/oauth/kakao/callback",
        ),
    )
    client = OAuthClientInformationFull(
        client_id="c-kakao", redirect_uris=["https://allowed.example/oauth/callback"]
    )
    await provider.register_client(client)

    result = await provider.authorize(client, _params())

    parts = urlsplit(result)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://kauth.kakao.com/oauth/authorize"
    )
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["rest-api-key"]
    assert query["redirect_uri"] == ["https://issuer.example/oauth/kakao/callback"]
    assert query["state"][0] in provider._pending_kakao_logins
    assert provider._pending_consents == {}


@pytest.mark.asyncio
async def test_kakao_login_callback_continues_to_local_consent(monkeypatch):
    async def exchange_stub(self, code):
        assert code == "kakao-code"
        return "kakao-access-token"

    async def subject_stub(self, access_token):
        assert access_token == "kakao-access-token"
        return "kakao:12345"

    monkeypatch.setattr(KakaoRestrictedOAuthProvider, "_exchange_kakao_code", exchange_stub)
    monkeypatch.setattr(KakaoRestrictedOAuthProvider, "_fetch_kakao_subject", subject_stub)
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        kakao_login_config=KakaoLoginConfig(
            rest_api_key="rest-api-key",
            client_secret=None,
            redirect_uri="https://issuer.example/oauth/kakao/callback",
        ),
    )
    client = OAuthClientInformationFull(
        client_id="c-kakao", redirect_uris=["https://allowed.example/oauth/callback"]
    )
    await provider.register_client(client)
    kakao_redirect = await provider.authorize(client, _params())
    state = parse_qs(urlsplit(kakao_redirect).query)["state"][0]

    consent_redirect = await provider.finish_kakao_login(state, "kakao-code")

    assert consent_redirect.startswith("/oauth/consent?token=")
    consent_token = parse_qs(urlsplit(consent_redirect).query)["token"][0]
    _, _, subject = provider._pending_consents[consent_token]
    assert subject == "kakao:12345"


@pytest.mark.asyncio
async def test_kakao_token_exchange_retries_without_stale_client_secret():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        kakao_login_config=KakaoLoginConfig(
            rest_api_key="rest-api-key",
            client_secret="stale-secret",
            redirect_uri="https://issuer.example/oauth/kakao/callback",
        ),
    )
    request = httpx.Request("POST", "https://kauth.kakao.com/oauth/token")
    calls = []

    async def post_stub(data):
        calls.append(dict(data))
        if len(calls) == 1:
            return httpx.Response(
                401,
                json={"error": "invalid_client"},
                request=request,
            )
        return httpx.Response(
            200,
            json={"access_token": "kakao-access-token"},
            request=request,
        )

    provider._post_kakao_token = post_stub

    token = await provider._exchange_kakao_code("kakao-code")

    assert token == "kakao-access-token"
    assert calls[0]["client_secret"] == "stale-secret"
    assert "client_secret" not in calls[1]


def test_oauth_runtime_diagnostics_do_not_expose_secrets():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        kakao_login_config=KakaoLoginConfig(
            rest_api_key="rest-api-key",
            client_secret="secret-value",
            redirect_uri="https://issuer.example/oauth/kakao/callback",
        ),
    )

    diagnostics = _oauth_runtime_diagnostics(provider)

    assert diagnostics == {
        "oauth_enabled": True,
        "external_login_enabled": True,
        "external_key_present": True,
        "external_key_suffix": "pi-key",
        "external_secret_present": True,
        "external_redirect_uri": "https://issuer.example/oauth/kakao/callback",
    }
    assert "secret-value" not in str(diagnostics)


@pytest.mark.asyncio
async def test_authorize_after_consent_issues_real_redirect():
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="c2", redirect_uris=["https://allowed.example/oauth/callback"]
    )
    await provider.register_client(client)

    result = await provider.finish_authorize(client, _params(), "subject-c2")

    assert result.startswith("https://allowed.example/oauth/callback")
    assert "code=" in result
    code = parse_qs(urlsplit(result).query)["code"][0]
    assert provider.auth_codes[code].subject == "subject-c2"


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
    redirect = await provider.finish_authorize(client, _params(), "subject-token")
    code = parse_qs(urlsplit(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)

    token = await provider.exchange_authorization_code(client, auth_code)

    restored = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        snapshot_path=path,
    )
    restored.snapshot_load()

    assert await restored.get_client("c-token") == client
    access = await restored.load_access_token(token.access_token)
    refresh = await restored.load_refresh_token(client, token.refresh_token)
    assert access is not None
    assert refresh is not None
    assert access.subject == "subject-token"
    assert refresh.subject == "subject-token"


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


def test_corrupt_oauth_snapshot_is_quarantined_instead_of_crashing(tmp_path):
    path = tmp_path / "oauth.json"
    path.write_text("{broken", encoding="utf-8")
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",),
        snapshot_path=path,
    )

    provider.snapshot_load()

    assert not path.exists()
    assert list(tmp_path.glob("oauth.json.corrupt.*"))
    assert provider.clients == {}


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


@pytest.mark.asyncio
async def test_revoke_static_client_consent_keeps_client_registration():
    """PlayMCP 고정 client는 연동 해제 후에도 재연동할 수 있어야 한다."""
    provider = KakaoRestrictedOAuthProvider(
        allowed_redirect_uris=("https://allowed.example/oauth/callback",)
    )
    client = OAuthClientInformationFull(
        client_id="static-client",
        client_secret="static-secret",
        redirect_uris=["https://allowed.example/oauth/callback"],
        token_endpoint_auth_method="client_secret_post",
    )
    provider.install_static_client(client)
    provider._consented_clients.add("static-client")

    await provider.revoke_client_consent("static-client")

    assert "static-client" not in provider._consented_clients
    assert await provider.get_client("static-client") == client
