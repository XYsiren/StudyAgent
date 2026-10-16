import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from test_api import register


@pytest.mark.parametrize('failure', ['', 'nonce', 'email', 'audience', 'collision'])
def test_oidc_pkce_signature_nonce_and_one_time_state(client, monkeypatch, failure):
    from app.api import oidc
    from app.models import OIDCFlow
    settings = client.app.state.settings
    if failure == 'collision':
        register(client, 'sso@example.com')
    settings.oidc_issuer, settings.oidc_client_id = 'https://identity.test', 'rag-client'
    metadata = {'issuer': 'https://identity.test', 'authorization_endpoint': 'https://identity.test/authorize',
                'token_endpoint': 'https://identity.test/token', 'jwks_uri': 'https://identity.test/keys'}
    monkeypatch.setattr(oidc, 'discovery', lambda _: metadata)
    response = client.get('/api/auth/oidc/start', follow_redirects=False)
    assert response.status_code == 307
    params = parse_qs(urlparse(response.headers['location']).query)
    assert params['code_challenge_method'] == ['S256']
    state = params['state'][0]
    with client.app.state.database.session() as db:
        flow = db.get(OIDCFlow, state)
        nonce, verifier = flow.nonce, flow.verifier
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {'iss': metadata['issuer'], 'sub': 'employee-1', 'aud': 'wrong-client' if failure == 'audience' else 'rag-client',
              'iat': int(time.time()), 'exp': int(time.time()) + 300, 'nonce': 'wrong' if failure == 'nonce' else nonce,
              'email': 'sso@example.com', 'email_verified': failure != 'email', 'name': 'SSO Member'}
    token = jwt.encode(claims, key, algorithm='RS256')
    def exchange(url, **kwargs):
        assert kwargs['data']['code_verifier'] == verifier
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'id_token': token})
    monkeypatch.setattr(oidc.httpx, 'post', exchange)
    monkeypatch.setattr(oidc.jwt, 'PyJWKClient', lambda *a, **k: SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())))
    response = client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'authorization-code'}, follow_redirects=False)
    assert response.status_code == (409 if failure == 'collision' else 401 if failure else 303), response.text
    assert client.get('/api/auth/oidc/callback', params={'state': state, 'code': 'same-code'}, follow_redirects=False).status_code == 400
    if not failure:
        assert client.get('/api/auth/me').json()['email'] == 'sso@example.com'


def test_oidc_rejects_login_csrf(client):
    register(client)
    assert client.get('/api/auth/oidc/callback', params={'state': 'foreign-state', 'code': 'c'}).status_code == 400


def test_oidc_discovery_requires_configured_https_issuer(client, monkeypatch):
    from app.api import oidc
    settings = client.app.state.settings
    settings.oidc_issuer = 'https://trusted.test'
    monkeypatch.setattr(oidc.httpx, 'get', lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'issuer': 'https://attacker.test'}))
    assert client.get('/api/auth/oidc/start').status_code == 503
