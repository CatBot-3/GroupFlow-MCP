"""Alexa+-style sign-in over real HTTP: the service tier (client credentials, mcp:service, resource indicator), a person's link
(authorization code with PKCE S256 on GroupFlow's own page), rotating refresh tokens, audience checks and revocation on the
person's page. Tokens are opaque and stored hashed; none of this can approve anything for anyone."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from groupflow.core.serve import serve_in_background
from groupflow.core.settings import ServerSettings
from groupflow.runtime import build_runtime
from tests.conftest import person

ACCEPT = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
REDIRECT = "https://alexa.example.com/auth/callback"


def init_body() -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "alexa-like", "version": "1"}}}


def parse(r: httpx.Response):
    if "event-stream" in r.headers.get("content-type", ""):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
    return r.json()


@pytest.fixture
def server(tmp_path):
    settings = ServerSettings(db_path=str(tmp_path / "gf.db"), admin_key=None, allowed_origins=[], tool_profile="voice", undeclared_audience="room")
    rt = build_runtime(settings, worker_interval_s=0.05)
    bg = serve_in_background(rt.app)
    bg.rt = rt
    bg.client = rt.services.engine.oauth_register_client("Alexa+", [REDIRECT])
    yield bg
    bg.stop()
    rt.db.close()


def basic(client: dict) -> dict:
    raw = f"{client['client_id']}:{client['client_secret']}".encode()
    return {"authorization": "Basic " + base64.b64encode(raw).decode()}


async def mcp(h: httpx.AsyncClient, base: str, token: str, method: str, params: dict | None = None, sid: str | None = None, rid: int = 2):
    headers = {**ACCEPT, "authorization": f"Bearer {token}"}
    if sid:
        headers.update({"mcp-session-id": sid, "mcp-protocol-version": "2025-03-26"})
    return await h.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, headers=headers)


async def session(h: httpx.AsyncClient, base: str, token: str) -> str:
    r = await h.post(f"{base}/mcp", json=init_body(), headers={**ACCEPT, "authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    await h.post(f"{base}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                 headers={**ACCEPT, "authorization": f"Bearer {token}", "mcp-session-id": sid, "mcp-protocol-version": "2025-03-26"})
    return sid


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def link(h: httpx.AsyncClient, server, member: str, verifier_challenge=None) -> dict:
    verifier, challenge = verifier_challenge or pkce()
    page = await h.get(f"{server.base}/oauth/authorize", params={
        "response_type": "code", "client_id": server.client["client_id"], "redirect_uri": REDIRECT, "state": "xyz",
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "mcp:resource", "resource": f"{server.base}/mcp"})
    assert page.status_code == 200 and "never</strong> confirm" in page.text and page.headers["x-frame-options"] == "DENY"
    ticket = re.search(r'name="ticket" value="([^"]+)"', page.text).group(1)
    wrong = await h.post(f"{server.base}/oauth/authorize", data={"ticket": ticket, "member_id": member, "passcode": "nope", "decision": "allow"})
    assert wrong.status_code == 400 and "Wrong member id or passcode" in wrong.text
    ok = await h.post(f"{server.base}/oauth/authorize", data={"ticket": ticket, "member_id": member, "passcode": person(member).passcode,
                                                              "decision": "allow"})
    assert ok.status_code == 302
    where = urlsplit(ok.headers["location"])
    q = parse_qs(where.query)
    assert f"{where.scheme}://{where.netloc}{where.path}" == REDIRECT and q["state"] == ["xyz"] and q["iss"] == [server.base]
    return {"code": q["code"][0], "verifier": verifier}


async def test_metadata_points_hosts_at_the_right_endpoints(server):
    async with httpx.AsyncClient() as h:
        prm = (await h.get(f"{server.base}/.well-known/oauth-protected-resource")).json()
        assert prm["resource"] == f"{server.base}/mcp" and prm["authorization_servers"] == [server.base]
        asm = (await h.get(f"{server.base}/.well-known/oauth-authorization-server")).json()
        assert asm["code_challenge_methods_supported"] == ["S256"] and "client_credentials" in asm["grant_types_supported"]
        r = await h.post(f"{server.base}/mcp", json=init_body(), headers=ACCEPT)
        assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]


async def test_the_service_tier_lists_tools_and_asks_for_a_person_to_run_one(server):
    async with httpx.AsyncClient() as h:
        bad = await h.post(f"{server.base}/oauth/token", data={"grant_type": "client_credentials", "scope": "mcp:service", "resource": f"{server.base}/mcp"},
                           headers={"authorization": "Basic " + base64.b64encode(f"{server.client['client_id']}:wrong".encode()).decode()})
        assert bad.status_code == 401 and bad.json()["error"] == "invalid_client"
        elsewhere = await h.post(f"{server.base}/oauth/token", data={"grant_type": "client_credentials", "scope": "mcp:service",
                                                                     "resource": "https://other.example/mcp"}, headers=basic(server.client))
        assert elsewhere.json()["error"] == "invalid_target"
        wide = await h.post(f"{server.base}/oauth/token", data={"grant_type": "client_credentials", "scope": "mcp:service mcp:resource",
                                                                "resource": f"{server.base}/mcp"}, headers=basic(server.client))
        assert wide.json()["error"] == "invalid_scope"
        tok = (await h.post(f"{server.base}/oauth/token", data={"grant_type": "client_credentials", "scope": "mcp:service",
                                                                "resource": f"{server.base}/mcp"}, headers=basic(server.client))).json()
        assert tok["expires_in"] <= 3600 and "refresh_token" not in tok and tok["scope"] == "mcp:service"
        sid = await session(h, server.base, tok["access_token"])
        listed = parse(await mcp(h, server.base, tok["access_token"], "tools/list", sid=sid))
        assert len(listed["result"]["tools"]) == 13
        run = await mcp(h, server.base, tok["access_token"], "tools/call", {"name": "catch_up", "arguments": {}}, sid=sid, rid=3)
        assert run.status_code == 403 and 'error="insufficient_scope"' in run.headers["www-authenticate"]


async def test_a_person_links_with_pkce_and_refresh_tokens_rotate(server):
    async with httpx.AsyncClient() as h:
        got = await link(h, server, "alice")
        wrong = await h.post(f"{server.base}/oauth/token", data={"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
                                                                 "code_verifier": secrets.token_urlsafe(48)}, headers=basic(server.client))
        assert wrong.json()["error"] == "invalid_grant"
        tok = (await h.post(f"{server.base}/oauth/token", data={"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
                                                                "code_verifier": got["verifier"]}, headers=basic(server.client))).json()
        assert tok["expires_in"] == 3600 and tok["refresh_token"].startswith("gfr_")
        sid = await session(h, server.base, tok["access_token"])
        brief = parse(await mcp(h, server.base, tok["access_token"], "tools/call", {"name": "catch_up", "arguments": {}}, sid=sid))
        assert brief["result"]["structuredContent"]["you"] == "Alice"
        # The code works once. A second use revokes what it produced.
        again = await h.post(f"{server.base}/oauth/token", data={"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
                                                                 "code_verifier": got["verifier"]}, headers=basic(server.client))
        assert again.json()["error"] == "invalid_grant"
        assert (await mcp(h, server.base, tok["access_token"], "tools/list", sid=sid)).status_code == 401

        got = await link(h, server, "alice")
        tok = (await h.post(f"{server.base}/oauth/token", data={"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
                                                                "code_verifier": got["verifier"]}, headers=basic(server.client))).json()
        fresh = (await h.post(f"{server.base}/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
                              headers=basic(server.client))).json()
        assert fresh["refresh_token"] != tok["refresh_token"] and fresh["access_token"] != tok["access_token"]
        stolen = await h.post(f"{server.base}/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
                              headers=basic(server.client))
        assert stolen.json()["error"] == "invalid_grant"  # a rotated token reused: the whole link is revoked
        r = await h.post(f"{server.base}/mcp", json=init_body(), headers={**ACCEPT, "authorization": f"Bearer {fresh['access_token']}"})
        assert r.status_code == 401


async def test_one_persons_token_never_reads_another_persons_private_plan_and_revoking_stops_the_next_call(server):
    async with httpx.AsyncClient() as h:
        got = await link(h, server, "linda")
        tok = (await h.post(f"{server.base}/oauth/token", data={"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
                                                                "code_verifier": got["verifier"]}, headers=basic(server.client))).json()
        sid = await session(h, server.base, tok["access_token"])
        party = parse(await mcp(h, server.base, tok["access_token"], "tools/call", {"name": "catch_up", "arguments": {"plan": "Linda's surprise party"}}, sid=sid))
        assert party["result"]["isError"] and "surprise" not in json.dumps(party["result"]["content"]).lower().replace("linda's surprise party", "")
        login = (await h.post(f"{server.base}/human/login", json={"member_id": "linda", "passcode": person("linda").passcode})).json()
        page = {"authorization": f"Bearer {login['token']}", "x-gf-csrf": login["csrf"]}
        links = (await h.get(f"{server.base}/human/links", headers=page)).json()["links"]
        grant = next(x for x in links if x.get("kind") == "oauth")
        assert grant["host"] == "Alexa+"
        assert (await h.post(f"{server.base}/human/links/revoke", json={"id": grant["id"]}, headers=page)).json()["outcome"] == "revoked"
        assert (await mcp(h, server.base, tok["access_token"], "tools/list", sid=sid)).status_code == 401


async def test_a_token_for_another_server_is_refused(server):
    engine = server.rt.services.engine
    async with httpx.AsyncClient() as h:
        got = await link(h, server, "bob")
        tok = (await h.post(f"{server.base}/oauth/token", data={"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
                                                                "code_verifier": got["verifier"]}, headers=basic(server.client))).json()
    engine.db.run("UPDATE oauth_tokens SET resource = 'https://other.example/mcp' WHERE kind = 'access'")
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{server.base}/mcp", json=init_body(), headers={**ACCEPT, "authorization": f"Bearer {tok['access_token']}"})
        assert r.status_code == 401
    engine.db.run(f"UPDATE oauth_tokens SET resource = '{server.base}/mcp', expires_at = '2000-01-01T00:00:00.000Z' WHERE kind = 'access'")
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{server.base}/mcp", json=init_body(), headers={**ACCEPT, "authorization": f"Bearer {tok['access_token']}"})
        assert r.status_code == 401  # expired


async def test_the_sign_in_page_refuses_bad_requests_without_redirecting(server):
    async with httpx.AsyncClient() as h:
        _, challenge = pkce()
        base = {"response_type": "code", "client_id": server.client["client_id"], "redirect_uri": REDIRECT, "code_challenge": challenge,
                "code_challenge_method": "S256"}
        for change in ({"redirect_uri": "https://evil.example/cb"}, {"code_challenge_method": "plain"}, {"client_id": "nobody"},
                       {"scope": "mcp:service"}, {"resource": "https://other.example/mcp"}):
            r = await h.get(f"{server.base}/oauth/authorize", params={**base, **change}, follow_redirects=False)
            assert r.status_code == 400 and "location" not in r.headers, change
        forged = await h.post(f"{server.base}/oauth/authorize", data={"ticket": "e30.abc", "member_id": "alice", "passcode": "x", "decision": "allow"})
        assert forged.status_code == 400
