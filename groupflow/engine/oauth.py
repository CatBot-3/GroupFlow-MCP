"""OAuth 2.1 sign-in for assistant hosts, in the two tiers Amazon documents for Alexa+ add-ons.

Tier 1, the service: a registered host gets a short-lived token with client_credentials, scope mcp:service and a resource
indicator naming this server. It can initialize and list tools, but no tool runs without a person, so a tool call answers 401
and the host starts account linking.

Tier 2, a person: authorization code with PKCE (S256 only). The person signs in on GroupFlow's own page with the same passcode
as their confirmation page and allows the host to act for them. Access tokens last an hour; refresh tokens rotate on every use,
and presenting a refresh token that was already rotated revokes the whole grant (a sign of a stolen token). The person can revoke
the link on their page at any time; the next MCP request then fails.

Tokens are opaque random strings stored only as hashes, bound to one client, one person (or none) and this server's resource.
Nothing here can approve, decline or withdraw for a person: that stays on the human-only API.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from groupflow.core.errors import DomainError
from groupflow.core.util import hash_secret

ACCESS_TTL_S = 3600          # Alexa+ requires at most an hour
REFRESH_TTL_S = 30 * 86400
CODE_TTL_S = 600
SERVICE_SCOPE = "mcp:service"
PERSON_SCOPES = ("mcp:resource", "mcp:resources", "mcp:tools", "groupflow")  # Amazon's docs use both spellings
GRANT_TYPES = ("authorization_code", "refresh_token", "client_credentials")


class OAuthError(Exception):
    """An RFC 6749 error response: error code, description and HTTP status."""

    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status

    def to_json(self) -> dict[str, str]:
        return {"error": self.error, "error_description": self.description}


@dataclass(frozen=True)
class AccessGrant:
    """What a valid access token stands for."""
    client_id: str
    client_name: str
    member_id: str | None  # None: the service tier, no person
    scope: str
    resource: str
    grant_id: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def same_resource(a: str, b: str) -> bool:
    return (a or "").rstrip("/").lower() == (b or "").rstrip("/").lower()


def pkce_matches(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier or "") <= 128:
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii", "ignore")).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge or "")


class OAuth:
    """Mixed into the engine: the same database as members, so a person's page can list and revoke their links."""

    # ------------------------------------------------------------ clients (registered by the operator)

    def oauth_register_client(self, name: str, redirect_uris: list[str], grant_types: list[str] | None = None,
                              scopes: list[str] | None = None, confidential: bool = True) -> dict[str, Any]:
        grants = list(grant_types or ["authorization_code", "refresh_token", "client_credentials"])
        if not set(grants) <= set(GRANT_TYPES):
            raise DomainError("INVALID_ARGUMENT", f"grant types are {', '.join(GRANT_TYPES)}")
        for uri in redirect_uris:
            if not (uri.startswith("https://") or uri.startswith("http://localhost") or uri.startswith("http://127.0.0.1")):
                raise DomainError("INVALID_ARGUMENT", "Redirect URIs must use https (or localhost for development).")
        client_id = "gfc_" + secrets.token_urlsafe(12)
        secret = "gfs_" + secrets.token_urlsafe(32) if confidential else None
        self.db.tx(lambda: self.db.run(
            "INSERT INTO oauth_clients (id, name, secret_hash, redirect_uris_json, grant_types_json, scopes_json, created_at) VALUES (?,?,?,?,?,?,?)",
            client_id, name.strip()[:60] or "Assistant", hash_secret(secret) if secret else None, json.dumps(redirect_uris), json.dumps(grants),
            json.dumps(scopes or [SERVICE_SCOPE, *PERSON_SCOPES]), _stamp(_now())))
        return {"client_id": client_id, "client_secret": secret, "name": name, "redirect_uris": redirect_uris, "grant_types": grants}

    def oauth_clients(self) -> list[dict[str, Any]]:
        return [{"client_id": r["id"], "name": r["name"], "redirect_uris": json.loads(r["redirect_uris_json"]),
                 "grant_types": json.loads(r["grant_types_json"]), "revoked": bool(r["revoked_at"])}
                for r in self.db.all("SELECT * FROM oauth_clients ORDER BY created_at")]

    def oauth_revoke_client(self, client_id: str) -> None:
        stamp = _stamp(_now())

        def run() -> None:
            self.db.run("UPDATE oauth_clients SET revoked_at = ? WHERE id = ?", stamp, client_id)
            self.db.run("UPDATE oauth_tokens SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL", stamp, client_id)
            self.db.run("UPDATE oauth_grants SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL", stamp, client_id)
        self.db.tx(run)

    def _client(self, client_id: str, secret: str | None, grant_type: str) -> dict[str, Any]:
        row = self.db.get("SELECT * FROM oauth_clients WHERE id = ? AND revoked_at IS NULL", client_id or "")
        if row is None:
            raise OAuthError("invalid_client", "Unknown client.", 401)
        if row["secret_hash"] and not hmac.compare_digest(row["secret_hash"], hash_secret(secret or "")):
            raise OAuthError("invalid_client", "Client authentication failed.", 401)
        if not row["secret_hash"] and grant_type == "client_credentials":
            raise OAuthError("unauthorized_client", "A public client cannot use client_credentials.")
        if grant_type not in json.loads(row["grant_types_json"]):
            raise OAuthError("unauthorized_client", f"This client may not use {grant_type}.")
        return row

    # ------------------------------------------------------------ tier 2: a person signs in and allows the host

    def oauth_check_authorize(self, client_id: str, redirect_uri: str, response_type: str, code_challenge: str,
                              code_challenge_method: str, scope: str, resource: str, expected_resource: str) -> dict[str, Any]:
        """Validate an /oauth/authorize request before showing the sign-in page. Errors that make the redirect unsafe raise
        OAuthError with status 400 and are shown on the page, never sent to an unverified redirect URI."""
        row = self.db.get("SELECT * FROM oauth_clients WHERE id = ? AND revoked_at IS NULL", client_id or "")
        if row is None:
            raise OAuthError("invalid_client", "Unknown client.")
        if redirect_uri not in json.loads(row["redirect_uris_json"]):
            raise OAuthError("invalid_request", "This redirect URI is not registered for the client.")
        if "authorization_code" not in json.loads(row["grant_types_json"]):
            raise OAuthError("unauthorized_client", "This client may not use the authorization code flow.")
        if response_type != "code":
            raise OAuthError("unsupported_response_type", "Only response_type=code is supported.")
        if code_challenge_method != "S256" or not 43 <= len(code_challenge or "") <= 128:
            raise OAuthError("invalid_request", "PKCE with code_challenge_method=S256 is required.")
        if resource and not same_resource(resource, expected_resource):
            raise OAuthError("invalid_target", "The resource is not this server.")
        return {"client": row, "scope": self._person_scope(scope)}

    @staticmethod
    def _person_scope(requested: str) -> str:
        wanted = [s for s in (requested or "").split() if s]
        if SERVICE_SCOPE in wanted:
            raise OAuthError("invalid_scope", "mcp:service is for the client credentials flow.")
        unknown = [s for s in wanted if s not in PERSON_SCOPES]
        if unknown:
            raise OAuthError("invalid_scope", f"Unknown scope: {' '.join(unknown)}.")
        return " ".join(wanted) or "mcp:resource"

    def oauth_issue_code(self, member_id: str, client_id: str, redirect_uri: str, code_challenge: str, scope: str, resource: str) -> str:
        code = "gfa_" + secrets.token_urlsafe(32)
        now = _now()
        grant_id = "grant_" + secrets.token_hex(6)

        def run() -> None:
            self.db.run("INSERT INTO oauth_grants (id, client_id, member_id, scope, resource, created_at) VALUES (?,?,?,?,?,?)",
                        grant_id, client_id, member_id, scope, resource, _stamp(now))
            self.db.run("INSERT INTO oauth_codes (code_hash, grant_id, redirect_uri, challenge, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                        hash_secret(code), grant_id, redirect_uri, code_challenge, _stamp(now), _stamp(now + timedelta(seconds=CODE_TTL_S)))
            client = self.db.get("SELECT name FROM oauth_clients WHERE id = ?", client_id)
            self._audit(member_id, "member.linked_host", "member", member_id, None,
                        f"{self.name(member_id)} let {client['name'] if client else 'an assistant'} act for them (OAuth).")
        self.db.tx(run)
        return code

    def _issue_tokens(self, client_id: str, member_id: str | None, scope: str, resource: str, grant_id: str | None,
                      refresh: bool) -> dict[str, Any]:
        now = _now()
        access = "gft_" + secrets.token_urlsafe(32)
        self.db.run("INSERT INTO oauth_tokens (token_hash, kind, grant_id, client_id, member_id, scope, resource, created_at, expires_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", hash_secret(access), "access", grant_id, client_id, member_id, scope, resource,
                    _stamp(now), _stamp(now + timedelta(seconds=ACCESS_TTL_S)))
        out: dict[str, Any] = {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL_S, "scope": scope}
        if refresh:
            token = "gfr_" + secrets.token_urlsafe(32)
            self.db.run("INSERT INTO oauth_tokens (token_hash, kind, grant_id, client_id, member_id, scope, resource, created_at, expires_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", hash_secret(token), "refresh", grant_id, client_id, member_id, scope, resource,
                        _stamp(now), _stamp(now + timedelta(seconds=REFRESH_TTL_S)))
            out["refresh_token"] = token
        return out

    def oauth_token(self, form: dict[str, str], client_id: str, client_secret: str | None, expected_resource: str) -> dict[str, Any]:
        grant_type = form.get("grant_type", "")
        if grant_type not in GRANT_TYPES:
            raise OAuthError("unsupported_grant_type", "Use authorization_code, refresh_token or client_credentials.")
        client = self._client(client_id, client_secret, grant_type)
        resource = form.get("resource") or ""
        if resource and not same_resource(resource, expected_resource):
            raise OAuthError("invalid_target", "The resource is not this server.")

        def run() -> dict[str, Any]:
            now = _stamp(_now())
            if grant_type == "client_credentials":
                scopes = [s for s in form.get("scope", "").split() if s]
                if scopes != [SERVICE_SCOPE]:
                    raise OAuthError("invalid_scope", "The service tier takes exactly the mcp:service scope.")
                if not resource:
                    raise OAuthError("invalid_target", "Name this server with the resource parameter.")
                return self._issue_tokens(client["id"], None, SERVICE_SCOPE, expected_resource, None, refresh=False)
            if grant_type == "authorization_code":
                row = self.db.get("SELECT * FROM oauth_codes WHERE code_hash = ?", hash_secret(form.get("code", "")))
                grant = self.db.get("SELECT * FROM oauth_grants WHERE id = ?", row["grant_id"]) if row else None
                if not row or not grant or grant["client_id"] != client["id"]:
                    raise OAuthError("invalid_grant", "Unknown authorization code.")
                if row["used_at"]:
                    # A code used twice: someone else may hold it. Everything it produced is revoked (after this transaction).
                    raise OAuthError("invalid_grant", "This authorization code was already used.")
                if row["expires_at"] < now:
                    raise OAuthError("invalid_grant", "The authorization code expired.")
                if row["redirect_uri"] != form.get("redirect_uri"):
                    raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request.")
                if not pkce_matches(form.get("code_verifier", ""), row["challenge"]):
                    raise OAuthError("invalid_grant", "PKCE verification failed.")
                if grant["revoked_at"]:
                    raise OAuthError("invalid_grant", "This link was revoked.")
                self.db.run("UPDATE oauth_codes SET used_at = ? WHERE code_hash = ?", now, row["code_hash"])
                return self._issue_tokens(client["id"], grant["member_id"], grant["scope"], grant["resource"], grant["id"],
                                          refresh="refresh_token" in json.loads(client["grant_types_json"]))
            # refresh_token: rotate; a rotated token presented again revokes the grant.
            old = self.db.get("SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'", hash_secret(form.get("refresh_token", "")))
            if not old or old["client_id"] != client["id"]:
                raise OAuthError("invalid_grant", "Unknown refresh token.")
            if old["replaced_by"] or old["revoked_at"]:
                # A rotated refresh token presented again: revoke the grant (after this transaction).
                raise OAuthError("invalid_grant", "This refresh token is no longer valid.")
            if old["expires_at"] < now:
                raise OAuthError("invalid_grant", "The refresh token expired.")
            grant = self.db.get("SELECT * FROM oauth_grants WHERE id = ?", old["grant_id"]) if old["grant_id"] else None
            if not grant or grant["revoked_at"]:
                raise OAuthError("invalid_grant", "This link was revoked.")
            scope = old["scope"]
            if form.get("scope"):
                narrower = [s for s in form["scope"].split() if s]
                if not set(narrower) <= set(scope.split()):
                    raise OAuthError("invalid_scope", "A refresh cannot widen the scope.")
                scope = " ".join(narrower)
            fresh = self._issue_tokens(client["id"], old["member_id"], scope, old["resource"], old["grant_id"], refresh=True)
            self.db.run("UPDATE oauth_tokens SET replaced_by = ? WHERE token_hash = ?", hash_secret(fresh["refresh_token"]), old["token_hash"])
            return fresh
        try:
            return self.db.tx(run)
        except OAuthError as err:
            if err.error == "invalid_grant" and ("already used" in err.description or "no longer valid" in err.description):
                self._revoke_on_reuse(form, grant_type)
            raise

    def _revoke_on_reuse(self, form: dict[str, str], grant_type: str) -> None:
        now = _stamp(_now())
        if grant_type == "authorization_code":
            row = self.db.get("SELECT grant_id FROM oauth_codes WHERE code_hash = ? AND used_at IS NOT NULL", hash_secret(form.get("code", "")))
        else:
            row = self.db.get("SELECT grant_id FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh' AND replaced_by IS NOT NULL",
                              hash_secret(form.get("refresh_token", "")))
        if row and row["grant_id"]:
            self.db.tx(lambda: self._revoke_grant(row["grant_id"], now))

    def _revoke_grant(self, grant_id: str, stamp: str) -> None:
        self.db.run("UPDATE oauth_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", stamp, grant_id)
        self.db.run("UPDATE oauth_tokens SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL", stamp, grant_id)

    def oauth_revoke_token(self, token: str, client_id: str, client_secret: str | None) -> None:
        """RFC 7009: always succeeds for the caller; revokes the token (a refresh token takes its whole grant with it)."""
        row = self.db.get("SELECT * FROM oauth_clients WHERE id = ? AND revoked_at IS NULL", client_id or "")
        if row is None or (row["secret_hash"] and not hmac.compare_digest(row["secret_hash"], hash_secret(client_secret or ""))):
            raise OAuthError("invalid_client", "Client authentication failed.", 401)
        tok = self.db.get("SELECT * FROM oauth_tokens WHERE token_hash = ? AND client_id = ?", hash_secret(token or ""), client_id)
        if tok is None:
            return
        stamp = _stamp(_now())
        if tok["kind"] == "refresh" and tok["grant_id"]:
            self.db.tx(lambda: self._revoke_grant(tok["grant_id"], stamp))
        else:
            self.db.tx(lambda: self.db.run("UPDATE oauth_tokens SET revoked_at = ? WHERE token_hash = ?", stamp, tok["token_hash"]))

    # ------------------------------------------------------------ the resource server side (every MCP request)

    def oauth_access(self, token: str, expected_resource: str) -> AccessGrant | None:
        """The grant behind a bearer token, or None. Checked on every request: expiry, revocation, audience."""
        if not (token or "").startswith("gft_"):
            return None
        row = self.db.get("SELECT t.*, c.name AS client_name, c.revoked_at AS client_revoked FROM oauth_tokens t JOIN oauth_clients c ON c.id = t.client_id "
                          "WHERE t.token_hash = ? AND t.kind = 'access'", hash_secret(token))
        if not row or row["revoked_at"] or row["client_revoked"] or row["expires_at"] < _stamp(_now()):
            return None
        if not same_resource(row["resource"], expected_resource):
            return None  # a token for another server is never accepted here
        if row["grant_id"]:
            grant = self.db.get("SELECT revoked_at FROM oauth_grants WHERE id = ?", row["grant_id"])
            if not grant or grant["revoked_at"]:
                return None
        return AccessGrant(client_id=row["client_id"], client_name=row["client_name"], member_id=row["member_id"], scope=row["scope"],
                           resource=row["resource"], grant_id=row["grant_id"])

    # ------------------------------------------------------------ the person's page

    def oauth_links(self, member_id: str) -> list[dict[str, Any]]:
        return [{"id": r["id"], "host": r["client_name"], "created_at": r["created_at"], "kind": "oauth", "scope": r["scope"]}
                for r in self.db.all("SELECT g.*, c.name AS client_name FROM oauth_grants g JOIN oauth_clients c ON c.id = g.client_id "
                                     "WHERE g.member_id = ? AND g.revoked_at IS NULL ORDER BY g.created_at", member_id)]

    def linked_hosts(self, member: Any) -> list[dict[str, Any]]:
        return [*super().linked_hosts(member), *self.oauth_links(member.id)]  # type: ignore[misc]

    def revoke_link(self, member: Any, link_id: str) -> dict[str, Any]:
        if not (link_id or "").startswith("grant_"):
            return super().revoke_link(member, link_id)  # type: ignore[misc]

        def write() -> dict[str, Any]:
            grant = self.db.get("SELECT * FROM oauth_grants WHERE id = ? AND member_id = ? AND revoked_at IS NULL", link_id, member.id)
            if not grant:
                raise DomainError("NOT_FOUND", "No such linked assistant.")
            self._revoke_grant(grant["id"], _stamp(_now()))
            self._audit(member.id, "member.unlinked_host", "member", member.id, None, f"{member.name} revoked an assistant link.")
            return {"outcome": "revoked"}
        return self.db.tx(write)


def sign_ticket(secret: bytes, params: dict[str, str]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).decode()
    mac = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def open_ticket(secret: bytes, ticket: str, max_age_s: int = 900) -> dict[str, str] | None:
    """The signed authorization request behind a sign-in form, or None if forged or older than max_age_s."""
    body, _, mac = (ticket or "").partition(".")
    if not body or not hmac.compare_digest(hmac.new(secret, body.encode(), hashlib.sha256).hexdigest(), mac):
        return None
    try:
        params = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    try:
        if _now().timestamp() - float(params.get("issued", 0)) > max_age_s:
            return None
    except (TypeError, ValueError):
        return None
    return params
