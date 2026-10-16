"""Accounts, groups and invitations: how people outside the demo world start using GroupFlow.

The demo world's people come from a file. Anyone else signs up on their own page, starts a group
or joins one with an invite code, and links an assistant host (an Alexa account, the group chat)
that then speaks for them over MCP with a token they can revoke. The rules follow the rest of
GroupFlow: an assistant may start a group or create an invite, but joining, leaving and linking
are things only the person does, on their own page, because each one changes what other people
can see about them or what can be done in their name.
"""
from __future__ import annotations

import re
import secrets
from datetime import timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from groupflow.core.errors import DomainError
from groupflow.core.util import hash_passcode, hash_secret, iso_now, new_id, new_secret, now

from .core import Row, _names
from .text import slugify

INVITE_TTL = timedelta(days=7)
INVITE_USES = 25
MAX_LINKS = 10
MAX_GROUPS_CREATED = 30
SIGNUPS_PER_MINUTE = 20
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: codes are read aloud and typed on phones
RESERVED_NAMES = {"everyone", "me", "myself", "you", "alexa", "assistant", "organizer", "nobody", "someone", "all", "group"}
PALETTE = ["#C98A00", "#2F6FB0", "#2F8A57", "#C0437A", "#6B46C1", "#2C7A7B", "#C05621", "#B83280", "#8A5A2B", "#2B6CB0"]


def _clean_name(value: str | None, what: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text or len(text) > max_len or any(ord(c) < 32 for c in text):
        raise DomainError("INVALID_ARGUMENT", f"{what} must be 1 to {max_len} characters.")
    return text


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def _stamp(delta: timedelta) -> str:
    return (now() + delta).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Membership:
    """Mixed into GroupFlow. Uses the core's rows, spaces, audit and notifications."""

    signup_mode: str = "open"  # open | invite | off
    join_base: str | None = None  # where an invite link opens, e.g. the group chat page

    # ================================================================ sign-up and the person's own view

    def sign_up(self, name: str, passcode: str, invite: str | None = None) -> dict[str, Any]:
        """Create a person and sign them in on their own page. With an invite code they also join that group."""
        if self.signup_mode == "off":
            raise DomainError("FORBIDDEN", "Sign-up is turned off on this server.")
        if self.signup_mode == "invite" and not invite:
            raise DomainError("FORBIDDEN", "This server only accepts sign-ups with an invite code.")
        clean = _clean_name(name, "Your name", 40)
        if clean.lower() in RESERVED_NAMES:
            raise DomainError("INVALID_ARGUMENT", f'"{clean}" is reserved. Pick another name.')
        if len(passcode or "") < 6 or len(passcode) > 128:
            raise DomainError("INVALID_ARGUMENT", "Choose a passcode of at least 6 characters.")
        self._rate_limit("__signup__", per_minute=SIGNUPS_PER_MINUTE)
        invite_row = self._usable_invite(invite) if invite else None

        def write() -> str:
            base = slugify(clean, 20) or "member"
            member_id, n = base, 1
            while self._member(member_id):
                n += 1
                member_id = f"{base}{n}"
            color = PALETTE[sum(map(ord, member_id)) % len(PALETTE)]
            # members.token_hash is the demo world's fixed token; a person who signs up uses linked tokens instead,
            # so this one is a random value nobody ever receives.
            self.db.run("INSERT INTO members (id, name, color, token_hash, passcode_hash) VALUES (?, ?, ?, ?, ?)",
                        member_id, clean, color, hash_secret(new_secret()), hash_passcode(passcode))
            self._audit(member_id, "member.signed_up", "member", member_id, None, f"{clean} signed up.")
            if invite_row:
                self._join(member_id, invite_row)
            return member_id

        member_id = self.db.tx(write)
        session = self.human_login(member_id, passcode)
        session["joined"] = self._group_brief(invite_row["group_id"]) if invite_row else None
        return session

    def human_me(self, member: Any) -> dict[str, Any]:
        """Who is signed in, their groups with members and roles, and the assistant hosts linked to them."""
        groups = []
        for g in self.db.all("SELECT g.*, gm.role FROM groups g JOIN group_members gm ON gm.group_id = g.id "
                             "WHERE gm.member_id = ? AND gm.status = 'active' ORDER BY g.rowid", member.id):
            brief = self._group_brief(g["id"])
            brief["your_role"] = g["role"]
            groups.append(brief)
        row = self._member(member.id) or {}
        return {"member": {"id": member.id, "name": member.name, "color": row.get("color")}, "groups": groups,
                "links": self.linked_hosts(member)}

    def _group_brief(self, group_id: str) -> dict[str, Any]:
        g = self._group(group_id)
        members = self.db.all("SELECT gm.member_id, gm.role, gm.label, m.name, m.color FROM group_members gm JOIN members m ON m.id = gm.member_id "
                              "WHERE gm.group_id = ? AND gm.status = 'active' ORDER BY gm.rowid", group_id)
        return {"id": g["id"], "name": g["name"], "timezone": g["timezone"], "currency": g["currency"], "description": g["description"],
                "members": [{"id": m["member_id"], "name": m["name"], "role": m["role"], "label": m["label"], "color": m["color"]} for m in members]}

    # ================================================================ groups

    def _my_group(self, actor_id: str, group: str | None) -> Row:
        groups = self.db.all("SELECT g.* FROM groups g JOIN group_members gm ON gm.group_id = g.id WHERE gm.member_id = ? AND gm.status = 'active' "
                             "ORDER BY g.rowid", actor_id)
        listeners = self._listeners(actor_id)
        if listeners:
            # In a shared conversation only the groups everyone listening belongs to are in play.
            groups = [g for g in groups if listeners <= self._active_member_ids(g["id"])]
        if not groups:
            raise DomainError("NOT_FOUND", "You are not in any group yet. Start one with create_group.")
        if group:
            low = group.strip().lower()
            hit = next((g for g in groups if g["id"] == group.strip() or g["name"].lower() == low), None)
            if hit is None:
                raise DomainError("NOT_FOUND", f'No group called "{group}" is available to you. Your groups: '
                                  + ", ".join(f"{g['name']} (id {g['id']})" for g in groups) + ".")
            return hit
        if len(groups) == 1:
            return groups[0]
        raise DomainError("AMBIGUOUS_GROUP", "Which group? Your groups: " + ", ".join(f"{g['name']} (id {g['id']})" for g in groups) + ". Pass group.",
                          {"groups": [{"id": g["id"], "name": g["name"]} for g in groups]})

    def _active_member_ids(self, group_id: str) -> set[str]:
        return {r["member_id"] for r in self.db.all("SELECT member_id FROM group_members WHERE group_id = ? AND status = 'active'", group_id)}

    def create_group(self, actor_id: str, name: str, timezone_name: str | None = None, currency: str | None = None,
                     description: str | None = None) -> dict[str, Any]:
        """Start a group with the speaker as its organizer. Nobody else is added: people join with an invite, on their own page."""
        self._rate_limit(actor_id)
        clean = _clean_name(name, "The group name", 60)
        if (self.db.scalar("SELECT COUNT(*) FROM audit WHERE actor_id = ? AND action = 'group.created'", actor_id) or 0) >= MAX_GROUPS_CREATED:
            raise DomainError("FORBIDDEN", f"You can start at most {MAX_GROUPS_CREATED} groups.")
        mine = self.db.get("SELECT g.* FROM groups g JOIN group_members gm ON gm.group_id = g.id WHERE gm.member_id = ? AND gm.status = 'active' "
                           "ORDER BY g.rowid LIMIT 1", actor_id)
        tz = (timezone_name or (mine or {}).get("timezone") or "America/Toronto").strip()
        try:
            ZoneInfo(tz)
        except Exception as err:  # noqa: BLE001 - any invalid zone name
            raise DomainError("INVALID_ARGUMENT", f"Unknown time zone {tz!r}; use an IANA name such as America/Toronto.") from err
        cur = (currency or (mine or {}).get("currency") or "CAD").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", cur):
            raise DomainError("INVALID_ARGUMENT", "Currency must be a three-letter code such as CAD.")
        if any(r["name"].lower() == clean.lower() for r in self.db.all(
                "SELECT g.name FROM groups g JOIN group_members gm ON gm.group_id = g.id WHERE gm.member_id = ? AND gm.status = 'active'", actor_id)):
            raise DomainError("DUPLICATE", f'You are already in a group called "{clean}". Pick another name.')

        def write() -> dict[str, Any]:
            base = slugify(clean, 24) or "group"
            gid = base
            while self._group(gid):
                gid = f"{base}_{secrets.token_hex(2)}"
            self.add_group(gid, clean, tz, cur, [{"id": actor_id, "role": "organizer"}], (description or "").strip()[:200] or None)
            self._audit(actor_id, "group.created", "group", gid, None, f"{self.name(actor_id)} started the group {clean}.", group_id=gid)
            return {"outcome": "created", "group": self._group_brief(gid),
                    "message": f"Started {clean} with you as organizer. Invite people with invite_to_group; each person joins on their own GroupFlow page."}
        return self.db.tx(write)

    def invite_to_group(self, actor_id: str, group: str | None = None) -> dict[str, Any]:
        """An organizer makes a shareable invite code. It adds nobody by itself: each person accepts it on their own page."""
        self._rate_limit(actor_id)

        def write() -> dict[str, Any]:
            g = self._my_group(actor_id, group)
            role = self._group_role(g["id"], actor_id)
            if not role or role["role"] != "organizer":
                organizers = [self.name(r["member_id"]) for r in self.db.all(
                    "SELECT member_id FROM group_members WHERE group_id = ? AND role = 'organizer' AND status = 'active'", g["id"])]
                raise DomainError("FORBIDDEN", f"Only an organizer of {g['name']} can invite people"
                                  + (f" ({_names(organizers)})." if organizers else "."))
            raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            code = f"{raw[:4]}-{raw[4:]}"
            expires = _stamp(INVITE_TTL)
            self.db.run("INSERT INTO group_invites (id, group_id, code_hash, created_by, created_at, expires_at, max_uses) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        new_id("inv"), g["id"], hash_secret(raw), actor_id, iso_now(), expires, INVITE_USES)
            self._audit(actor_id, "group.invite_created", "group", g["id"], None, f"{self.name(actor_id)} created an invite to {g['name']}.", group_id=g["id"])
            out = {"outcome": "created", "group": {"id": g["id"], "name": g["name"]}, "code": code, "expires_at": expires,
                   "message": f"Invite code for {g['name']}: {code}. Share it; each person opens it and joins on their own GroupFlow page. "
                              "Nobody can be added for them."}
            if self.join_base:
                out["join_url"] = f"{self.join_base}?invite={code}"
            return out
        return self.db.tx(write)

    def _usable_invite(self, code: str | None) -> Row:
        raw = normalize_code(code or "")
        row = self.db.get("SELECT * FROM group_invites WHERE code_hash = ?", hash_secret(raw)) if len(raw) == 8 else None
        if not row or row["revoked_at"] or row["expires_at"] < iso_now() or row["uses"] >= row["max_uses"] or not self._group(row["group_id"]):
            raise DomainError("NOT_FOUND", "That invite code is not valid any more. Ask an organizer for a new one.")
        return row

    def invite_preview(self, code: str) -> dict[str, Any]:
        """What an invite link shows before signing in: the group's name, who invited, and how many people are in it."""
        self._rate_limit("__invite_preview__", per_minute=120)
        row = self._usable_invite(code)
        g = self._group(row["group_id"])
        return {"group": {"id": g["id"], "name": g["name"]}, "invited_by": self.name(row["created_by"]),
                "members": len(self._active_member_ids(g["id"])), "expires_at": row["expires_at"]}

    def accept_invite(self, member: Any, code: str) -> dict[str, Any]:
        """The invited person joins, on their own page. Plans shared with the whole group become visible to them."""
        def write() -> dict[str, Any]:
            row = self._usable_invite(code)
            if member.id in self._active_member_ids(row["group_id"]):
                return {"outcome": "already_member", "group": self._group_brief(row["group_id"]),
                        "message": f"You are already in {self._group(row['group_id'])['name']}."}
            self._join(member.id, row)
            g = self._group(row["group_id"])
            return {"outcome": "joined", "group": self._group_brief(g["id"]),
                    "message": f"You joined {g['name']}. Plans shared with the whole group are now visible to you."}
        return self.db.tx(write)

    def _join(self, member_id: str, invite: Row) -> None:
        gid = invite["group_id"]
        existing = self.db.get("SELECT * FROM group_members WHERE group_id = ? AND member_id = ?", gid, member_id)
        if existing:
            self.db.run("UPDATE group_members SET status = 'active', role = 'member', membership_version = membership_version + 1 "
                        "WHERE group_id = ? AND member_id = ?", gid, member_id)
        else:
            self.db.run("INSERT INTO group_members (group_id, member_id, role) VALUES (?, ?, 'member')", gid, member_id)
        everyone = f"{gid}.everyone"
        if self._space(everyone):
            self.db.run("INSERT OR IGNORE INTO space_members (space_id, member_id) VALUES (?, ?)", everyone, member_id)
        private = f"{gid}.private.{member_id}"
        if not self._space(private):
            self.add_space(private, gid, f"{self.name(member_id)}'s private notes", "private", [member_id])
        self.db.run("UPDATE group_invites SET uses = uses + 1 WHERE id = ?", invite["id"])
        g = self._group(gid)
        others = [m for m in self._active_member_ids(gid) if m != member_id]
        self._notify(others, None, "member_joined", f"{self.name(member_id)} joined {g['name']}.", space_id=everyone if self._space(everyone) else None)
        self._audit(member_id, "group.joined", "group", gid, None, f"{self.name(member_id)} joined {g['name']} with an invite from {self.name(invite['created_by'])}.",
                    group_id=gid)

    def leave_group(self, member: Any, group: str) -> dict[str, Any]:
        """The person leaves, on their own page: shared plans of the group stop being visible to them at once.
        A version already out for confirmation must be sent again, because the people it was made for changed."""
        def write() -> dict[str, Any]:
            row = self.db.get("SELECT g.* FROM groups g JOIN group_members gm ON gm.group_id = g.id WHERE gm.member_id = ? AND gm.status = 'active' "
                              "AND (g.id = ? OR lower(g.name) = lower(?))", member.id, group, group)
            if not row:
                raise DomainError("NOT_FOUND", "You are not in that group.")
            gid = row["id"]
            was_organizer = (self._group_role(gid, member.id) or {}).get("role") == "organizer"
            self.db.run("UPDATE group_members SET status = 'left', role = 'member', membership_version = membership_version + 1 "
                        "WHERE group_id = ? AND member_id = ?", gid, member.id)
            for s in self.db.all("SELECT id FROM spaces WHERE group_id = ? AND kind = 'shared'", gid):
                self.db.run("DELETE FROM space_members WHERE space_id = ? AND member_id = ?", s["id"], member.id)
            remaining = [r["member_id"] for r in self.db.all(
                "SELECT member_id FROM group_members WHERE group_id = ? AND status = 'active' ORDER BY rowid", gid)]
            handed: list[str] = []
            if was_organizer and remaining and not any((self._group_role(gid, m) or {}).get("role") == "organizer" for m in remaining):
                self.db.run("UPDATE group_members SET role = 'organizer' WHERE group_id = ? AND member_id = ?", gid, remaining[0])
                handed.append(remaining[0])
            for p in self.db.all("SELECT * FROM plans WHERE group_id = ? AND organizer_id = ? AND status <> 'CANCELLED'", gid, member.id):
                audience = self.plan_audience(p)
                if not audience or member.id in audience:  # nobody left to hand it to, or their own private plan
                    continue
                successor = next((m for m in audience if (self._group_role(gid, m) or {}).get("role") == "organizer"), audience[0])
                self.db.run("UPDATE plans SET organizer_id = ? WHERE id = ?", successor, p["id"])
                self._audit(member.id, "plan.organizer_changed", "plan", p["id"], p, f"{self.name(member.id)} left; {self.name(successor)} now organizes {p['title']}.")
                handed.append(successor)
            everyone = f"{gid}.everyone"
            self._notify(remaining, None, "member_left", f"{member.name} left {row['name']}."
                         + (f" {_names([self.name(m) for m in dict.fromkeys(handed)])} took over organizing." if handed else ""),
                         space_id=everyone if self._space(everyone) else None)
            self._audit(member.id, "group.left", "group", gid, None, f"{member.name} left {row['name']}.", group_id=gid)
            return {"outcome": "left", "group": {"id": gid, "name": row["name"]}, "message": f"You left {row['name']}."}
        return self.db.tx(write)

    # ================================================================ assistant links (account linking)

    def link_host(self, member: Any, host: str) -> dict[str, Any]:
        """The person lets an assistant host act for them over MCP. The token is shown once, to the host; it can be revoked here."""
        label = _clean_name(host, "The assistant's name", 40)

        def write() -> dict[str, Any]:
            active = self.db.scalar("SELECT COUNT(*) FROM linked_tokens WHERE member_id = ? AND revoked_at IS NULL", member.id) or 0
            if active >= MAX_LINKS:
                raise DomainError("FORBIDDEN", f"You have {MAX_LINKS} linked assistants. Revoke one on your page first.")
            token = "gfl_" + new_secret(24)
            link_id = new_id("lnk")
            self.db.run("INSERT INTO linked_tokens (id, member_id, host, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                        link_id, member.id, label, hash_secret(token), iso_now())
            self._audit(member.id, "member.linked_host", "member", member.id, None, f"{member.name} let {label} act for them.")
            return {"id": link_id, "host": label, "token": token,
                    "message": f"{label} can now act for you over MCP. It can never confirm, decline or withdraw for you; revoke it here at any time."}
        return self.db.tx(write)

    def linked_hosts(self, member: Any) -> list[dict[str, Any]]:
        return [{"id": r["id"], "host": r["host"], "created_at": r["created_at"]} for r in self.db.all(
            "SELECT * FROM linked_tokens WHERE member_id = ? AND revoked_at IS NULL ORDER BY created_at", member.id)]

    def revoke_link(self, member: Any, link_id: str) -> dict[str, Any]:
        def write() -> dict[str, Any]:
            n = self.db.run("UPDATE linked_tokens SET revoked_at = ? WHERE id = ? AND member_id = ? AND revoked_at IS NULL", iso_now(), link_id, member.id)
            if not n:
                raise DomainError("NOT_FOUND", "No such linked assistant.")
            self._audit(member.id, "member.unlinked_host", "member", member.id, None, f"{member.name} revoked an assistant link.")
            return {"outcome": "revoked"}
        return self.db.tx(write)
