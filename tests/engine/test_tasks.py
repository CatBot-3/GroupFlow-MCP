"""Tasks, links, blockers and the topic search."""
from groupflow.engine.text import canonical, parse_count, same_value
from tests.conftest import code, item_id, task

SUNDAY = "Sunday, Oct 25, 6:00 pm"


def test_a_root_blocker_is_found_through_the_chain(gf):
    gf.propose_change("alice", item="party.date", value=SUNDAY)
    e = gf.explain("charlie", item="Send invitations")
    assert e.blockers and e.blockers[0].root == "Book Rosa's back room"
    assert e.blockers[0].owner == "Alice"
    assert e.blockers[0].path == ["Send invitations", "Book Rosa's back room"]
    assert set(e.blockers[0].holds_up) >= {"Send invitations", "Decorate the back room", "Drive Linda to the party"}


def test_nothing_blocks_at_the_start(gf):
    e = gf.explain("charlie", item="Send invitations")
    assert e.blockers == [] and "Nothing is blocking" in e.message
    assert gf.explain("charlie").message == "Nothing is blocked right now."


def test_finishing_the_root_unblocks_and_notifies(gf):
    gf.propose_change("alice", item="party.date", value=SUNDAY)
    r = gf.propose_change("alice", item="Book Rosa's back room", status="done", quote="Rebooked for Sunday")
    assert r.outcome == "updated"
    assert sorted(r.impacts.unblocked) == ["Decorate the back room", "Drive Linda to the party", "Send invitations"]
    assert any("no longer blocked" in u for u in gf.context("charlie").updates)


def test_a_pending_decision_is_a_blocker_named_after_its_owner(gf):
    gf.propose_change("charlie", item="party.date", value=SUNDAY)
    e = gf.explain("bob", item="Send invitations")
    assert "Alice" in e.message


def test_loops_are_rejected(gf):
    invites, booking = item_id(gf, "Send invitations"), item_id(gf, "Book Rosa's back room")
    assert code(lambda: gf.link_items("alice", booking, invites)) == "CYCLE_REJECTED"
    assert code(lambda: gf.link_items("alice", booking, booking)) == "CYCLE_REJECTED"


def test_links_can_be_removed_and_are_idempotent(gf):
    porch, cake = item_id(gf, "Fix the porch light"), item_id(gf, "Order the cake")
    assert gf.link_items("bob", porch, cake).outcome == "linked"
    assert gf.link_items("bob", porch, cake).outcome == "already_linked"
    assert gf.link_items("bob", porch, cake, remove=True).outcome == "unlinked"


def test_activities_cannot_depend_on_things_and_details_cannot_wait_on_tasks(gf):
    assert code(lambda: gf.link_items("alice", "dinner", "party.date")) in ("INVALID_ARGUMENT", "NOT_FOUND")
    assert code(lambda: gf.link_items("alice", "party.venue", item_id(gf, "Order the cake"))) == "INVALID_ARGUMENT"


def test_non_owner_task_changes_go_to_the_owner(gf):
    invites = item_id(gf, "Send invitations")
    r = gf.propose_change("bob", item=invites, status="done")
    assert r.outcome == "pending_review" and "Charlie" in r.message
    assert task(gf, "charlie", "Send invitations").status == "todo"
    req = gf.context("charlie").decisions_for_you[0]
    assert gf.decide_change("charlie", req.request, "accept").outcome == "accepted"
    assert task(gf, "charlie", "Send invitations").status == "done"


def test_organizers_may_change_any_task(gf):
    r = gf.propose_change("alice", item="Send invitations", due="Friday")
    assert r.outcome == "updated" and task(gf, "charlie", "Send invitations").due == "Friday"
    assert gf.propose_change("alice", item="Send invitations", due="").outcome == "updated"
    assert task(gf, "charlie", "Send invitations").due is None


def test_handing_a_task_on_notifies_the_new_owner(gf):
    gf.propose_change("charlie", item="Decorate the back room", owner="bob")
    assert task(gf, "bob", "Decorate the back room").owner == "Bob"
    assert any('handed you "Decorate the back room"' in u for u in gf.context("bob").updates)


def test_tasks_watching_an_activity_wait_until_it_is_agreed(gf):
    t = task(gf, "dana", "Book the minivan")
    assert t.blocked_by and "not agreed yet" in t.blocked_by[0]


def test_finished_work_reopens_when_what_it_relied_on_changes(gf):
    r = gf.propose_change("alice", item="party.date", value=SUNDAY)
    assert "Book Rosa's back room" in r.impacts.reopened
    t = task(gf, "alice", "Book Rosa's back room")
    assert t.status == "todo" and t.flags and "Reopened" in t.flags[0]
    # Updating your own task acknowledges its flags.
    gf.propose_change("alice", item="Book Rosa's back room", status="in_progress")
    assert task(gf, "alice", "Book Rosa's back room").flags == []


def test_dates_compare_by_meaning():
    assert same_value("datetime", canonical("datetime", "Sunday the 25th at 6"), canonical("datetime", SUNDAY))
    assert not same_value("datetime", canonical("datetime", "Sunday at 5"), canonical("datetime", SUNDAY))
    assert canonical("count", "eighteen people") == canonical("count", "18 guests")
    assert parse_count("twenty five") == 25


def test_search_finds_items_by_words_and_roles(gf):
    s = gf.search("alice", "cake")
    assert {h.label for h in s.items} >= {"Cake", "Order the cake", "Pick up the cake"}
    assert "Drive Linda to the party" in {h.label for h in gf.search("alice", "Mom").items}
    assert gf.search("linda", "cake").matched is False


def test_search_finds_catalog_places(gf):
    s = gf.search("chen", "vegetarian restaurant")
    assert {p.name for p in s.places} >= {"Restaurant A", "Restaurant B"}
