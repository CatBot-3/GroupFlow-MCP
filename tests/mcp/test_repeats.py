"""A host that resends a write (a timeout, a retried voice turn) must not double money, tasks or reminders. The server treats the same
member sending the same write with the same arguments, onto plans exactly as that write left them, within two minutes, as a retry:
it returns the first result and offers to record it again."""
from __future__ import annotations

import pytest

from groupflow.core.errors import DomainError
from groupflow.engine import ASSISTANT
from tests.conftest import seeded


def run(gf, member: str, tool: str, **args):
    """What ToolRunner does for an MCP call: capture the write with its arguments, as the assistant."""
    token = ASSISTANT.set(True)
    try:
        return gf.capture_action(member, tool, lambda: getattr(gf, tool)(member, **args), arguments=args)
    finally:
        ASSISTANT.reset(token)


@pytest.fixture
def gf():
    return seeded()


def expenses(gf, plan: str) -> int:
    return gf.db.scalar("SELECT COUNT(*) FROM expenses WHERE plan_id = ? AND status = 'active'", plan) or 0


def test_a_resent_expense_is_recorded_once_and_can_be_recorded_again_on_request(gf):
    before = expenses(gf, "friday_dinner")
    first = run(gf, "alice", "costs", action="add", plan="friday_dinner", amount=40, what="parking")
    second = run(gf, "alice", "costs", action="add", plan="friday_dinner", amount=40, what="parking")
    assert expenses(gf, "friday_dinner") == before + 1
    assert second["replayed"] is True and second["repeat"]["of"] == first["action_receipt"]["id"]
    offer = second["suggested_followup"]
    assert offer["options"] == ["yes, again", "no"]
    token = ASSISTANT.set(True)
    try:
        answered = gf.answer_followup("alice", "yes, again", followup=offer["id"])
    finally:
        ASSISTANT.reset(token)
    assert answered.get("outcome") != "declined", answered
    assert expenses(gf, "friday_dinner") == before + 2


def test_a_new_task_is_not_created_twice(gf):
    def tasks():
        return gf.db.scalar("SELECT COUNT(*) FROM items WHERE plan_id = 'friday_dinner' AND shape = 'task' AND archived = 0")
    n = tasks()
    run(gf, "alice", "propose_change", plan="friday_dinner", new_type="task", title="Buy candles", quote="add buy candles")
    run(gf, "alice", "propose_change", plan="friday_dinner", new_type="task", title="Buy candles", quote="add buy candles")
    assert tasks() == n + 1


def test_anything_in_between_makes_it_a_new_request(gf):
    before = expenses(gf, "friday_dinner")
    run(gf, "alice", "costs", action="add", plan="friday_dinner", amount=12, what="ice")
    run(gf, "bob", "bring_list", action="bring", things=["napkins"], plan="friday_dinner")  # someone else wrote meanwhile
    again = run(gf, "alice", "costs", action="add", plan="friday_dinner", amount=12, what="ice")
    assert not again.get("replayed") and expenses(gf, "friday_dinner") == before + 2


def test_different_people_and_explicit_request_keys_are_unaffected(gf):
    before = expenses(gf, "friday_dinner")
    run(gf, "alice", "costs", action="add", plan="friday_dinner", amount=9, what="chips")
    run(gf, "bob", "costs", action="add", plan="friday_dinner", amount=9, what="chips")
    assert expenses(gf, "friday_dinner") == before + 2
    token = gf.state_token("friday_dinner")
    keyed = gf.apply_action("alice", "costs", {"action": "add", "plan": "friday_dinner", "amount": 5, "what": "lime"}, "k-1", token)
    replay = gf.apply_action("alice", "costs", {"action": "add", "plan": "friday_dinner", "amount": 5, "what": "lime"}, "k-1", token)
    assert replay["replayed"] and replay["action_receipt"]["id"] == keyed["action_receipt"]["id"]
    with pytest.raises(DomainError):
        gf.apply_action("alice", "costs", {"action": "add", "plan": "friday_dinner", "amount": 6, "what": "lime"}, "k-1", token)
