"""OAuth endpoints for assistant hosts: metadata (RFC 9728, RFC 8414), the person's sign-in page, token and revocation.

The sign-in page is GroupFlow's own: the person enters the same member id and passcode as on their confirmation page and allows
the host to act for them. The host never sees the passcode. Every form carries a signed, short-lived ticket of the original
request, so a forged or replayed form cannot change the client, redirect URI or PKCE challenge.
"""
from __future__ import annotations

import base64
import html
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from groupflow.engine.oauth import PERSON_SCOPES, SERVICE_SCOPE, OAuthError, open_ticket, sign_ticket

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
PAGE_HEADERS = {**NO_STORE, "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
                "Referrer-Policy": "no-referrer"}


def server_base(request: Request, public_url: str | None) -> str:
    if public_url:
        return public_url.rstrip("/")
    host = request.headers.get("host") or request.url.netloc
    return f"{request.url.scheme}://{host}"


def resource_url(request: Request, public_url: str | None) -> str:
    return server_base(request, public_url) + "/mcp"


async def form_of(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


def client_credentials(request: Request, form: dict[str, str]) -> tuple[str, str | None]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth[6:].strip()).decode("utf-8")
            cid, _, secret = raw.partition(":")
            from urllib.parse import unquote
            return unquote(cid), unquote(secret)
        except (ValueError, UnicodeDecodeError):
            return "", None
    return form.get("client_id", ""), form.get("client_secret")


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem;color:#1d1d1f;background:#fbfbfd}}
h1{{font-size:1.4rem}} label{{display:block;margin:.8rem 0 .2rem}} input{{width:100%;padding:.55rem;font-size:1rem;box-sizing:border-box}}
.row{{display:flex;gap:.6rem;margin-top:1.2rem}} button{{flex:1;padding:.6rem;font-size:1rem;cursor:pointer}}
.note{{background:#eef3ff;border-radius:8px;padding:.7rem .9rem}} .err{{color:#b00020}}
</style></head><body>{body}</body></html>"""
    return HTMLResponse(doc, status_code=status, headers=PAGE_HEADERS)


def register_oauth_routes(app: FastAPI, services: Any, public_url: str | None = None) -> None:
    engine = services.engine
    identity = services.identity
    ticket_key = secrets.token_bytes(32)
    attempts: dict[str, list[float]] = {}

    def throttled(key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in attempts.get(key, []) if t > now - 60]
        attempts[key] = recent
        if len(recent) >= 8:
            return True
        recent.append(now)
        return False

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def protected_resource(request: Request) -> JSONResponse:
        base = server_base(request, public_url)
        return JSONResponse({"resource": base + "/mcp", "authorization_servers": [base], "bearer_methods_supported": ["header"],
                             "scopes_supported": [SERVICE_SCOPE, *PERSON_SCOPES], "resource_name": "GroupFlow"})

    @app.get("/.well-known/oauth-authorization-server")
    async def authorization_server(request: Request) -> JSONResponse:
        base = server_base(request, public_url)
        return JSONResponse({"issuer": base, "authorization_endpoint": base + "/oauth/authorize", "token_endpoint": base + "/oauth/token",
                             "revocation_endpoint": base + "/oauth/revoke", "response_types_supported": ["code"],
                             "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
                             "code_challenge_methods_supported": ["S256"],
                             "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
                             "revocation_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
                             "scopes_supported": [SERVICE_SCOPE, *PERSON_SCOPES], "authorization_response_iss_parameter_supported": True})

    def sign_in_form(ticket: str, client_name: str, error: str = "", member_id: str = "") -> HTMLResponse:
        err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
        return _page("Sign in to GroupFlow", f"""<h1>Let {html.escape(client_name)} use GroupFlow for you</h1>
<p class="note">It will be able to read the plans you can see and act for you there. It can <strong>never</strong> confirm, decline
or withdraw for you: you do that yourself, on your own GroupFlow page. You can revoke this link on your page at any time.</p>{err}
<form method="post" action="/oauth/authorize">
<input type="hidden" name="ticket" value="{html.escape(ticket)}">
<label for="member">Your GroupFlow member id</label><input id="member" name="member_id" autocomplete="username" value="{html.escape(member_id)}" required>
<label for="pass">Passcode</label><input id="pass" name="passcode" type="password" autocomplete="current-password" required>
<div class="row"><button name="decision" value="allow" type="submit">Allow</button><button name="decision" value="deny" type="submit" formnovalidate>Cancel</button></div>
</form>""", 200 if not error else 400)

    @app.get("/oauth/authorize")
    async def authorize_page(request: Request) -> HTMLResponse:
        q = dict(request.query_params)
        try:
            checked = engine.oauth_check_authorize(q.get("client_id", ""), q.get("redirect_uri", ""), q.get("response_type", ""),
                                                   q.get("code_challenge", ""), q.get("code_challenge_method", ""), q.get("scope", ""),
                                                   q.get("resource", ""), resource_url(request, public_url))
        except OAuthError as err:
            return _page("Cannot sign in", f"<h1>This sign-in link is not valid</h1><p class='err'>{html.escape(err.description)}</p>", 400)
        ticket = sign_ticket(ticket_key, {"client_id": q["client_id"], "redirect_uri": q["redirect_uri"], "state": q.get("state", ""),
                                          "challenge": q["code_challenge"], "scope": checked["scope"], "resource": resource_url(request, public_url),
                                          "issued": str(time.time())})
        return sign_in_form(ticket, checked["client"]["name"])

    @app.post("/oauth/authorize")
    async def authorize_submit(request: Request):
        form = await form_of(request)
        params = open_ticket(ticket_key, form.get("ticket", ""))
        if params is None:
            return _page("Cannot sign in", "<h1>This sign-in form expired</h1><p>Start again from your assistant.</p>", 400)
        client = engine.db.get("SELECT name FROM oauth_clients WHERE id = ? AND revoked_at IS NULL", params["client_id"])
        if client is None:
            return _page("Cannot sign in", "<h1>This assistant is no longer registered</h1>", 400)
        base = server_base(request, public_url)

        def back(**extra: str) -> RedirectResponse:
            query = {**extra, **({"state": params["state"]} if params.get("state") else {}), "iss": base}
            sep = "&" if "?" in params["redirect_uri"] else "?"
            return RedirectResponse(params["redirect_uri"] + sep + urlencode(query), status_code=302, headers=NO_STORE)

        if form.get("decision") != "allow":
            return back(error="access_denied")
        member_id = (form.get("member_id") or "").strip().lower()
        if throttled(member_id or (request.client.host if request.client else "?")):
            return sign_in_form(form["ticket"], client["name"], "Too many attempts. Wait a minute and try again.", member_id)
        member = identity.by_passcode(member_id, form.get("passcode", ""))
        if member is None:
            return sign_in_form(form["ticket"], client["name"], "Wrong member id or passcode.", member_id)
        code = engine.oauth_issue_code(member.id, params["client_id"], params["redirect_uri"], params["challenge"], params["scope"], params["resource"])
        return back(code=code)

    @app.post("/oauth/token")
    async def token(request: Request) -> JSONResponse:
        form = await form_of(request)
        cid, secret = client_credentials(request, form)
        try:
            return JSONResponse(engine.oauth_token(form, cid, secret, resource_url(request, public_url)), headers=NO_STORE)
        except OAuthError as err:
            headers = {**NO_STORE, **({"WWW-Authenticate": 'Basic realm="groupflow"'} if err.status == 401 else {})}
            return JSONResponse(err.to_json(), status_code=err.status, headers=headers)

    @app.post("/oauth/revoke")
    async def revoke(request: Request) -> JSONResponse:
        form = await form_of(request)
        cid, secret = client_credentials(request, form)
        try:
            engine.oauth_revoke_token(form.get("token", ""), cid, secret)
        except OAuthError as err:
            return JSONResponse(err.to_json(), status_code=err.status, headers=NO_STORE)
        return JSONResponse({}, headers=NO_STORE)
