"""Accounts, groups, invitations and assistant links (engine level), and the listener restriction for shared conversations."""
from __future__ import annotations

import pytest

from groupflow.core.errors import DomainError
from groupflow.engine import LISTENERS
from tests.conftest import code, seeded


@pytest.fixture
def gf():
    return seeded()


def as_listeners(speaker: str, *others: str):
    """Run engine calls as if the host said these people will also hear the reply."""
    return LISTENERS.set((speaker, frozenset({speaker, *others})))


def test_sign_up_hashes_the_passcode_and_signs_the_person_in(gf):
    s = gf.sign_up("Maya Lin", "correct horse")
    assert s["member"]["name"] == "Maya Lin" and s["token"] and s["csrf"]
    row = gf.db.get("SELECT * FROM members WHERE id = ?", s["member"]["id"])
    assert row["passcode_hash"].startswith("scrypt$") and "correct horse" not in row["passcode_hash"]
    assert gf.identity.by_passcode(s["member"]["id"], "correct horse") is not None
    assert gf.identity.by_passcode(s["member"]["id"], "wrong horse") is None
    # The demo world's plain-digest passcodes keep working.
    assert gf.identity.by_passcode("alice", "alice-demo") is not None
    # Ids stay unique and readable when names repeat.
    again = gf.sign_up("Maya Lin", "another one")
    assert again["member"]["id"] != s["member"]["id"]


def test_sign_up_rules(gf):
    assert code(lambda: gf.sign_up("", "secret12")) == "INVALID_ARGUMENT"
    assert code(lambda: gf.sign_up("Everyone", "secret12")) == "INVALID_ARGUMENT"
    assert code(lambda: gf.sign_up("Sam Short", "123")) == "INVALID_ARGUMENT"
    gf.signup_mode = "invite"
    assert code(lambda: gf.sign_up("No Code", "secret12")) == "FORBIDDEN"
    gf.signup_mode = "off"
    assert code(lambda: gf.sign_up("Nope", "secret12")) == "FORBIDDEN"


def test_a_new_group_has_only_its_creator_and_invites_are_organizer_only(gf):
    maya = gf.human_session(gf.sign_up("Maya", "secret12")["token"])
    res = gf.create_group(maya.id, "Flatmates", timezone_name="Europe/London", currency="gbp")
    g = res["group"]
    assert res["outcome"] == "created" and g["timezone"] == "Europe/London" and g["currency"] == "GBP"
    assert [m["id"] for m in g["members"]] == [maya.id] and g["members"][0]["role"] == "organizer"
    assert code(lambda: gf.create_group(maya.id, "Flatmates")) == "DUPLICATE"
    assert code(lambda: gf.create_group(maya.id, "Somewhere", timezone_name="Mars/Olympus")) == "INVALID_ARGUMENT"
    inv = gf.invite_to_group(maya.id, g["id"])
    assert inv["code"][4] == "-" and len(inv["code"]) == 9 and inv["outcome"] == "created"
    # Codes are stored hashed: the database never holds a usable code.
    assert gf.db.scalar("SELECT COUNT(*) FROM group_invites WHERE code_hash = ?", inv["code"]) == 0
    theo = gf.human_session(gf.sign_up("Theo", "secret12", invite=inv["code"])["token"])
    assert code(lambda: gf.invite_to_group(theo.id, g["id"])) == "FORBIDDEN"


def test_joining_shows_whole_group_plans_but_not_plans_kept_from_the_newcomer(gf):
    maya = gf.human_session(gf.sign_up("Maya", "secret12")["token"])
    gid = gf.create_group(maya.id, "Flatmates")["group"]["id"]
    gf.create_plan(maya.id, "shared_tasks", "Flat jobs", group=gid)
    preview_code = gf.invite_to_group(maya.id, gid)["code"]
    preview = gf.invite_preview(preview_code.lower().replace("-", " "))
    assert preview["group"]["name"] == "Flatmates" and preview["invited_by"] == "Maya" and preview["members"] == 1
    theo = gf.human_session(gf.sign_up("Theo", "secret12")["token"])
    assert gf.accept_invite(theo, preview_code)["outcome"] == "joined"
    assert gf.accept_invite(theo, preview_code)["outcome"] == "already_member"
    # A plan made before or after joining for the whole group is visible; one hidden from the newcomer is not.
    assert "Flat jobs" in [p["title"] for p in gf.scope_for(theo.id).plans.values()]
    gf.create_plan(maya.id, "gathering", "Theo's birthday", group=gid, hidden_from=["Theo"])
    assert "Theo's birthday" not in [p["title"] for p in gf.scope_for(theo.id).plans.values()]
    # Everyone already there hears who joined.
    assert any("Theo joined Flatmates" in u for u in gf.context(maya.id, mark_read=False).updates)
    assert code(lambda: gf.accept_invite(theo, "AAAA-AAAA")) == "NOT_FOUND"


def test_expired_and_used_up_invites_are_refused(gf):
    maya = gf.human_session(gf.sign_up("Maya", "secret12")["token"])
    gid = gf.create_group(maya.id, "Flatmates")["group"]["id"]
    c1 = gf.invite_to_group(maya.id, gid)["code"]
    gf.db.run("UPDATE group_invites SET expires_at = '2000-01-01T00:00:00.000Z'")
    assert code(lambda: gf.invite_preview(c1)) == "NOT_FOUND"
    c2 = gf.invite_to_group(maya.id, gid)["code"]
    gf.db.run("UPDATE group_invites SET uses = max_uses WHERE expires_at > '2001'")
    assert code(lambda: gf.sign_up("Late", "secret12", invite=c2)) == "NOT_FOUND"


def test_leaving_removes_visibility_and_hands_over_organizing(gf):
    maya = gf.human_session(gf.sign_up("Maya", "secret12")["token"])
    gid = gf.create_group(maya.id, "Flatmates")["group"]["id"]
    invite = gf.invite_to_group(maya.id, gid)["code"]
    theo = gf.human_session(gf.sign_up("Theo", "secret12", invite=invite)["token"])
    plan = gf.create_plan(maya.id, "gathering", "Housewarming", group=gid).plan.id
    res = gf.leave_group(maya, gid)
    assert res["outcome"] == "left"
    assert plan not in gf.scope_for(maya.id).plans
    assert gf._plan_row(plan)["organizer_id"] == theo.id
    assert gf._group_role(gid, theo.id)["role"] == "organizer"
    assert any("Maya left Flatmates" in u for u in gf.context(theo.id, mark_read=False).updates)
    assert code(lambda: gf.leave_group(maya, gid)) == "NOT_FOUND"


def test_a_membership_change_stops_a_version_out_for_confirmation_from_committing(gf):
    """Joining changes who the plan is for, so a version sent before must be sent again (unchanged answers carry forward)."""
    alice = gf.identity.get("alice")
    gf.create_group("alice", "Board games")
    gid = gf.db.scalar("SELECT id FROM groups WHERE name = 'Board games'")
    plan = gf.create_plan("alice", "general_event", "Game night", group=gid).plan.id
    gf.propose_change("alice", plan=plan, new_type="activity", title="Games", day="0", start="19:00", end="21:00", place="Alice's place")
    gf.request_confirmations("alice", plan=plan)
    from groupflow.world import approve_all
    approve_all(gf, alice, plan)
    inv = gf.invite_to_group("alice", gid)["code"]
    gf.sign_up("Newcomer", "secret12", invite=inv)
    res = gf.commit_plan("alice", plan=plan)
    assert res.outcome == "blocked" and any("membership" in b for b in res.blockers)


def test_linked_tokens_identify_the_person_until_revoked(gf):
    s = gf.sign_up("Maya", "secret12")
    maya = gf.human_session(s["token"])
    link = gf.link_host(maya, "GroupFlow chat")
    assert link["token"].startswith("gfl_")
    assert gf.identity.by_token(link["token"]).id == maya.id
    assert [x["host"] for x in gf.human_me(maya)["links"]] == ["GroupFlow chat"]
    gf.revoke_link(maya, link["id"])
    assert gf.identity.by_token(link["token"]) is None
    assert code(lambda: gf.revoke_link(maya, link["id"])) == "NOT_FOUND"


# ---------------------------------------------------------------- listeners


def test_listeners_hide_a_surprise_from_a_room_its_guest_is_in(gf):
    token = as_listeners("alice", "bob", "charlie", "linda")
    try:
        titles = [p["title"] for p in gf.scope_for("alice").plans.values()]
        assert "Linda's surprise party" not in titles and "Home" in titles
        assert code(lambda: gf.context("alice", plan="party")) == "NOT_FOUND"
        assert code(lambda: gf.propose_change("alice", item="party.date", value="Sunday, Oct 25, 6:00 pm")) == "NOT_FOUND"
        ctx = gf.context("alice", detail="summary", mark_read=False)
        assert "surprise" not in ctx.model_dump_json().lower()
        assert ctx.shared_with == ["Bob", "Charlie", "Linda"] and "privately" in ctx.shared_note
        # Other people's scopes are untouched by the speaker's listeners.
        assert "Linda's surprise party" in [p["title"] for p in gf.scope_for("bob").plans.values()]
    finally:
        LISTENERS.reset(token)
    # Without listeners Alice sees it again.
    assert "Linda's surprise party" in [p["title"] for p in gf.scope_for("alice").plans.values()]


def test_listeners_keep_the_speakers_own_private_conditions_out_of_the_reply(gf):
    plain = gf.context("chen", plan="friday_dinner", mark_read=False)
    assert plain.your_conditions  # Chen's own limits, when only Chen hears the reply
    token = as_listeners("chen", "alice", "bob", "dana")
    try:
        ctx = gf.context("chen", plan="friday_dinner", mark_read=False)
        assert ctx.your_conditions == []
        assert "nut" not in ctx.model_dump_json().lower()
        gf.set_my_conditions("chen", [{"kind": "home_by", "value": {"time": "22:00"}}], plan="friday_dinner")
        todo = [t for t in gf.context("chen", plan="friday_dinner", mark_read=False).your_todos if t.kind == "verify_condition"]
        assert todo and "22" not in todo[0].text
    finally:
        LISTENERS.reset(token)


def test_plans_started_in_a_shared_conversation_must_be_visible_to_everyone_listening(gf):
    token = as_listeners("alice", "bob", "charlie", "linda")
    try:
        assert code(lambda: gf.create_plan("alice", "general_event", "Gift ideas", private=True)) == "SHARED_CONVERSATION"
        assert code(lambda: gf.create_plan("alice", "gathering", "Mom's party", hidden_from=["Linda"])) == "SHARED_CONVERSATION"
        # Alice is in two groups, but only the family contains everyone listening, so it is chosen without asking.
        res = gf.create_plan("alice", "general_event", "Sunday lunch")
        assert gf._plan_row(res.plan.id)["group_id"] == "family"
    finally:
        LISTENERS.reset(token)


def test_resolve_listeners_validates_the_declaration(gf):
    assert gf.resolve_listeners("alice", "group:family") == frozenset({"alice", "bob", "charlie", "linda"})
    assert gf.resolve_listeners("alice", "bob, chen") == frozenset({"alice", "bob", "chen"})
    with pytest.raises(ValueError):
        gf.resolve_listeners("alice", "group:team")  # not her group
    with pytest.raises(ValueError):
        gf.resolve_listeners("alice", "nobody_here")
    # Only the speaker listening is no restriction at all.
    token = LISTENERS.set(("alice", frozenset({"alice"})))
    try:
        assert gf._listeners("alice") is None
    finally:
        LISTENERS.reset(token)


def test_unheard_notes_wait_for_a_private_conversation(gf):
    """A note from outside the room is neither read out nor marked heard in the shared conversation."""
    maya = gf.human_session(gf.sign_up("Maya", "secret12")["token"])
    gid = gf.create_group(maya.id, "Flatmates")["group"]["id"]
    gf.sign_up("Theo", "secret12", invite=gf.invite_to_group(maya.id, gid)["code"])
    gf._notify([maya.id], None, "misc", "A note that belongs to no space.")
    token = as_listeners(maya.id, "theo")
    try:
        shared = gf.context(maya.id, detail="summary")
        assert all("belongs to no space" not in u for u in shared.updates)
    finally:
        LISTENERS.reset(token)
    assert any("belongs to no space" in u for u in gf.context(maya.id, detail="summary").updates)


def test_domain_errors_carry_codes(gf):
    with pytest.raises(DomainError):
        gf.invite_to_group("alice", "No such group")
