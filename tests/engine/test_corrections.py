"""Independent correction cases C01-C08; frozen manifest precedes source inspection.

Synthetic in-memory fixtures only. Direct SQL is used only to inject record
changes for which the public ledger has no edit operation.
"""
from __future__ import annotations

import json

import pytest

from groupflow.engine import LISTENERS
from tests.conftest import code


def guarded(gf, tool, arguments, *, key="original", actor="alice"):
    return gf.apply_action(actor, tool, arguments, key, gf.state_token(arguments["plan"]))


def bring(gf, label="test cups", plan="party"):
    result = guarded(gf, "bring_list", {"plan": plan, "action": "bring", "things": [label]})
    assert result["action_receipt"]["reversible"]
    row = gf.db.get("SELECT * FROM bring_items WHERE plan_id=? AND label=?", plan, label)
    return result["action_receipt"]["id"], row


def expense(gf, description="test supplies", plan="party"):
    result = guarded(gf, "costs", {"plan": plan, "action": "add", "amount": "20.00", "what": description,
                                   "split_with": ["Alice", "Bob"]})
    assert result["action_receipt"]["reversible"]
    row = gf.db.get("SELECT * FROM expenses WHERE plan_id=? AND description=?", plan, description)
    return result["action_receipt"]["id"], row


def stored_state(gf):
    tables = ("bring_items", "expenses", "settlements", "approval_receipts", "action_receipts", "audit", "notifications")
    return {table: sorted(gf.db.all(f"SELECT * FROM {table}"), key=lambda row: json.dumps(row, sort_keys=True))
            for table in tables}


def reject_without_effects(gf, receipt):
    before = stored_state(gf)
    assert code(lambda: gf.correct_action("alice", receipt)) == "STALE"
    assert stored_state(gf) == before
    assert gf.db.scalar("SELECT corrected_by FROM action_receipts WHERE id=?", receipt) is None


@pytest.mark.parametrize("kind", ["bring", "expense"])
def test_unrelated_later_bring_work_does_not_block_correction(gf, kind):
    """C01: the old whole-plan token cannot prevent this safe local correction."""
    receipt, row = bring(gf) if kind == "bring" else expense(gf)
    gf.bring_list("bob", "bring", ["unrelated napkins"], plan="party")
    later = gf.db.get("SELECT * FROM bring_items WHERE label='unrelated napkins'")
    result = gf.correct_action("alice", receipt)
    assert result["action_receipt"]["id"] != receipt
    assert gf.db.get("SELECT * FROM bring_items WHERE id=?", later["id"]) == later
    if kind == "bring":
        assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE id=?", row["id"]) is None
    else:
        assert gf.db.scalar("SELECT status FROM expenses WHERE id=?", row["id"]) == "removed"


def test_unrelated_later_expense_does_not_block_bring_correction(gf):
    receipt, row = bring(gf)
    gf.costs("bob", "add", plan="party", amount="7.00", what="unrelated taxi", split_with=["Bob"])
    later = gf.db.get("SELECT * FROM expenses WHERE description='unrelated taxi'")
    gf.correct_action("alice", receipt)
    assert gf.db.get("SELECT * FROM expenses WHERE id=?", later["id"]) == later
    assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE id=?", row["id"]) is None


def test_reassigned_bring_record_blocks_old_correction(gf):
    """C02: correction cannot release the new person's independent promise."""
    receipt, row = bring(gf)
    gf.bring_list("alice", "unclaim", [row["id"]], plan="party")
    gf.bring_list("bob", "claim", [row["id"]], plan="party")
    reject_without_effects(gf, receipt)
    assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE id=?", row["id"]) == "bob"


def test_release_and_reclaim_by_original_actor_is_a_new_bring_dependency(gf):
    """C02 ABA: identical final owner does not restore the original promise."""
    receipt, row = bring(gf)
    gf.bring_list("alice", "unclaim", [row["id"]], plan="party")
    gf.bring_list("alice", "claim", [row["id"]], plan="party")
    reject_without_effects(gf, receipt)
    assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE id=?", row["id"]) == "alice"


@pytest.mark.parametrize("column,value", [("amount_minor", 2500), ("description", "corrected supplies")])
def test_changed_expense_record_blocks_old_correction(gf, column, value):
    """C03: preserve a later edit rather than silently removing that record."""
    receipt, row = expense(gf)
    gf.db.tx(lambda: gf.db.run(f"UPDATE expenses SET {column}=? WHERE id=?", value, row["id"]))
    reject_without_effects(gf, receipt)
    assert gf.db.scalar(f"SELECT {column} FROM expenses WHERE id=?", row["id"]) == value


def test_changed_expense_split_blocks_old_correction(gf):
    """C04: the split lives in shares_json, and is part of the dependency."""
    receipt, row = expense(gf)
    changed = json.dumps({"alice": 500, "bob": 1500})
    gf.db.tx(lambda: gf.db.run("UPDATE expenses SET shares_json=? WHERE id=?", changed, row["id"]))
    reject_without_effects(gf, receipt)
    assert gf.db.scalar("SELECT shares_json FROM expenses WHERE id=?", row["id"]) == changed


@pytest.mark.parametrize("active", [False, True])
def test_related_pending_or_active_settlement_blocks_expense_correction(gf, active):
    """C05: even a pending repayment is dependent financial work to preserve."""
    receipt, row = expense(gf)
    if active:
        gf.costs("alice", "paid", plan="party", amount="10.00", paid_by="Bob", to="me")
    else:
        gf.costs("bob", "paid", plan="party", amount="10.00", to="Alice")
    settlements = gf.db.all("SELECT * FROM settlements WHERE plan_id='party'")
    assert settlements
    reject_without_effects(gf, receipt)
    assert gf.db.all("SELECT * FROM settlements WHERE plan_id='party'") == settlements
    assert gf.db.scalar("SELECT status FROM expenses WHERE id=?", row["id"]) == "active"


@pytest.mark.parametrize("kind", ["bring", "expense"])
def test_related_activity_approval_revocation_blocks_correction(gf, kind):
    """C06: explicit Dinner labels bind to Dinner's approval dependencies."""
    receipt, _ = (bring(gf, "Dinner napkins", "friday_dinner") if kind == "bring"
                  else expense(gf, "Dinner supplies", "friday_dinner"))
    approval = gf.db.get("SELECT r.* FROM approval_receipts r JOIN approval_scopes s ON s.id=r.scope_id "
                         "WHERE r.plan_id='friday_dinner' AND r.member_id='bob' AND s.activity_id='dinner' "
                         "AND r.status='valid'")
    assert approval
    gf.human_revoke(gf.identity.get("bob"), approval["id"])
    reject_without_effects(gf, receipt)


def test_unrelated_activity_approval_revocation_does_not_block_correction(gf):
    """C06: a Dessert approval is unrelated to the explicit Dinner bring row."""
    receipt, row = bring(gf, "Dinner napkins", "friday_dinner")
    approval = gf.db.get("SELECT r.* FROM approval_receipts r JOIN approval_scopes s ON s.id=r.scope_id "
                         "WHERE r.plan_id='friday_dinner' AND r.member_id='bob' AND s.activity_id='dessert' "
                         "AND r.status='valid'")
    assert approval
    gf.human_revoke(gf.identity.get("bob"), approval["id"])
    gf.correct_action("alice", receipt)
    assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE id=?", row["id"]) is None
    assert gf.db.scalar("SELECT status FROM approval_receipts WHERE id=?", approval["id"]) == "revoked"


@pytest.mark.parametrize("kind", ["bring", "expense"])
def test_repeat_correction_is_idempotent_even_after_unrelated_later_work(gf, kind):
    """C07: a second correction must not produce another audit or receipt."""
    receipt, _ = bring(gf) if kind == "bring" else expense(gf)
    result = gf.correct_action("alice", receipt)
    gf.bring_list("bob", "bring", ["later tablecloth"], plan="party")
    before = stored_state(gf)
    again = gf.correct_action("alice", receipt)
    assert again["outcome"] == "already_corrected"
    assert again["receipt"] == result["action_receipt"]["id"]
    assert stored_state(gf) == before


def test_correction_requires_original_actor_and_original_conversation_audience(gf):
    """C08: even an otherwise visible plan cannot reveal another scope's receipt."""
    receipt, _ = bring(gf)
    before = stored_state(gf)
    assert code(lambda: gf.correct_action("bob", receipt)) == "NOT_FOUND"
    audience = LISTENERS.set(("alice", frozenset({"alice", "bob"})))
    try:
        assert gf.scope_for("alice").sees_plan("party")
        assert code(lambda: gf.correct_action("alice", receipt)) == "NOT_FOUND"
    finally:
        LISTENERS.reset(audience)
    assert stored_state(gf) == before


def test_removed_member_cannot_correct_or_repeat_a_previous_correction(gf):
    """C08: idempotence cannot bypass current visibility checks."""
    receipt, _ = bring(gf)
    gf.correct_action("alice", receipt)
    group = gf.db.scalar("SELECT group_id FROM plans WHERE id='party'")
    gf.leave_group(gf.identity.get("alice"), group)
    before = stored_state(gf)
    assert code(lambda: gf.correct_action("alice", receipt)) == "NOT_FOUND"
    assert stored_state(gf) == before


# Supplemental final-backend cases. Frozen separately in SUPPLEMENTAL_MANIFEST.md.


@pytest.mark.parametrize("label", ["x", "qq"])
def test_supplemental_short_label_frozen_clock_release_reclaim_aba_blocks(gf, label):
    """S01: unchanged final row and timestamp cannot hide a new promise."""
    from groupflow.engine import fixed_clock

    gf.set_clock(fixed_clock("2026-10-18T12:00:00Z"))
    receipt, row = bring(gf, label)
    gf.bring_list("alice", "unclaim", [row["id"]], plan="party")
    gf.bring_list("alice", "claim", [row["id"]], plan="party")
    assert gf.db.get("SELECT * FROM bring_items WHERE id=?", row["id"]) == row
    epochs = gf.db.all("SELECT * FROM record_epochs ORDER BY record_type,record_id")
    reject_without_effects(gf, receipt)
    assert gf.db.all("SELECT * FROM record_epochs ORDER BY record_type,record_id") == epochs


def test_supplemental_expense_amount_changed_and_restored_still_blocks(gf):
    """S02: financial ABA must be detected even with the exact old final row."""
    receipt, row = expense(gf)
    gf.db.tx(lambda: gf.db.run("UPDATE expenses SET amount_minor=? WHERE id=?", row["amount_minor"] + 100, row["id"]))
    gf.db.tx(lambda: gf.db.run("UPDATE expenses SET amount_minor=? WHERE id=?", row["amount_minor"], row["id"]))
    assert gf.db.get("SELECT * FROM expenses WHERE id=?", row["id"]) == row
    epochs = gf.db.all("SELECT * FROM record_epochs ORDER BY record_type,record_id")
    reject_without_effects(gf, receipt)
    assert gf.db.all("SELECT * FROM record_epochs ORDER BY record_type,record_id") == epochs


@pytest.mark.parametrize("label", ["x", "qq"])
def test_supplemental_unrelated_bring_label_sharing_short_word_does_not_block(gf, label):
    """S03: lexical overlap with another row cannot become a dependency."""
    # Two longer labels make the later short request unambiguous as a new row;
    # IDs then identify each existing row through the supported bring API.
    gf.bring_list("bob", "bring", [f"{label} napkins", f"{label} plates"], plan="party")
    receipt, own = bring(gf, label)
    other = gf.db.get("SELECT * FROM bring_items WHERE label=? AND plan_id='party'", f"{label} napkins")
    assert other and other["id"] != own["id"]
    gf.bring_list("bob", "unclaim", [other["id"]], plan="party")
    gf.bring_list("bob", "claim", [other["id"]], plan="party")
    later = gf.db.get("SELECT * FROM bring_items WHERE id=?", other["id"])
    gf.correct_action("alice", receipt)
    assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE id=?", own["id"]) is None
    assert gf.db.get("SELECT * FROM bring_items WHERE id=?", other["id"]) == later


@pytest.mark.parametrize("depth", [1, 2])
def test_supplemental_linked_activity_dependency_change_blocks_correction(gf, depth):
    """S04: supported direct/transitive item links guard explicit Dinner work."""
    previous = "dinner"
    for index in range(depth):
        title = f"Check vouchers {index}"
        gf.propose_change("alice", plan="friday_dinner", new_type="task", title=title, owner="alice")
        linked = gf.db.get("SELECT * FROM items WHERE plan_id='friday_dinner' AND label=?", title)
        assert linked and linked["shape"] == "task"
        assert gf.link_items("alice", linked["id"], previous, plan="friday_dinner").outcome == "linked"
        previous = linked["id"]
    receipt, _ = bring(gf, "Dinner napkins", "friday_dinner")
    assert gf.propose_change("alice", item=previous, plan="friday_dinner", status="done").outcome == "updated"
    changed = gf.db.get("SELECT * FROM items WHERE id=?", previous)
    reject_without_effects(gf, receipt)
    assert gf.db.get("SELECT * FROM items WHERE id=?", previous) == changed


def test_supplemental_additive_receipts_migration_preserves_all_tables_and_tracks_old_rows(gf, tmp_path):
    """S05: open a reconstructed old schema containing complete synthetic data."""
    import hashlib
    import sqlite3

    from groupflow.core.db import Db, _SCHEMAS

    _, old_bring = bring(gf, "legacy marker")
    gf.costs("alice", "add", plan="party", amount="14.00", what="legacy expense marker")
    old_expense = gf.db.get("SELECT * FROM expenses WHERE description='legacy expense marker'")
    legacy_sql = "\n".join(sql for name, sql in _SCHEMAS if name != "reliability_v1")
    legacy_hash = hashlib.sha256(legacy_sql.encode()).hexdigest()[:16]
    path = str(tmp_path / "reconstructed-before-reliability.db")

    def tables(connection):
        return {row["name"]: row["sql"] for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}

    def rows(connection, table):
        return sorted([dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')],
                      key=lambda row: json.dumps(row, sort_keys=True))

    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    try:
        raw.executescript(legacy_sql + "\nCREATE TABLE gf_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);")
        old_schema = tables(raw)
        assert "record_epochs" not in old_schema
        for table in old_schema:
            if table in {"gf_meta", "sqlite_sequence"}:
                continue
            source = gf.db.all(f'SELECT * FROM "{table}"')
            if source:
                columns = list(source[0])
                column_sql = ",".join(f'"{column}"' for column in columns)
                placeholders = ",".join("?" for _ in columns)
                raw.executemany(f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})',
                                [tuple(row[column] for column in columns) for row in source])
        raw.execute("INSERT INTO gf_meta VALUES ('schema',?)", (legacy_hash,))
        raw.commit()
        before = {table: rows(raw, table) for table in old_schema if table != "gf_meta"}
        before_triggers = {row["name"] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert rows(raw, "bring_items") and rows(raw, "expenses") and rows(raw, "approval_receipts")
    finally:
        raw.close()

    migrated = Db(path)
    try:
        assert tables(migrated.raw) == {**old_schema, "record_epochs": tables(migrated.raw)["record_epochs"]}
        assert {table: sorted(migrated.all(f'SELECT * FROM "{table}"'), key=lambda row: json.dumps(row, sort_keys=True))
                for table in before} == before
        assert migrated.scalar("SELECT value FROM gf_meta WHERE key='schema'") != legacy_hash
        after_triggers = {row["name"] for row in migrated.all("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert before_triggers <= after_triggers
        assert after_triggers - before_triggers
        assert migrated.all("PRAGMA foreign_key_check") == []

        for table, row, column, changed in [("bring_items", old_bring, "claimed_by", None),
                                            ("expenses", old_expense, "amount_minor", old_expense["amount_minor"] + 100)]:
            initial = migrated.scalar("SELECT version FROM record_epochs WHERE record_type=? AND record_id=?", table, row["id"]) or 0
            migrated.tx(lambda: migrated.run(f'UPDATE "{table}" SET "{column}"=? WHERE id=?', changed, row["id"]))
            first = migrated.scalar("SELECT version FROM record_epochs WHERE record_type=? AND record_id=?", table, row["id"])
            assert first is not None and first > initial
            migrated.tx(lambda: migrated.run(f'UPDATE "{table}" SET "{column}"=? WHERE id=?', row[column], row["id"]))
            restored = migrated.scalar("SELECT version FROM record_epochs WHERE record_type=? AND record_id=?", table, row["id"])
            assert restored > first
            assert migrated.get(f'SELECT * FROM "{table}" WHERE id=?', row["id"]) == row
    finally:
        migrated.close()
