"""Templates: every kind of plan is data, validated when it loads."""
import pytest
import yaml
from pydantic import ValidationError

from groupflow.templates import Template, load_templates

EXPECTED = {"general_event", "gathering", "evening_out", "trip", "meeting", "shared_tasks", "study_group", "project"}


def test_canonical_directory_loads():
    assert set(load_templates()) == EXPECTED


def test_each_template_offers_at_least_one_owner_or_open_item_type():
    for t in load_templates().values():
        assert any(it.policy in ("owner", "open") for it in t.item_types), t.id


def test_templates_with_activities_have_conditions_and_a_search_domain():
    for t in load_templates().values():
        if t.has_activities:
            assert t.conditions and t.search.max_options >= 4, t.id


def test_a_template_cannot_give_activities_the_owner_policy():
    bad = {"id": "x", "name": "X", "description": "", "item_types": [{"name": "a", "label": "A", "shape": "activity", "policy": "owner"}]}
    with pytest.raises(ValidationError):
        Template.model_validate(bad)


def test_a_template_cannot_start_with_an_unknown_place():
    t = yaml.safe_load(open(next(p for p in __import__("pathlib").Path("groupflow/templates").glob("evening_out.yaml"))))
    t["starter"]["activities"][1]["place"] = "nowhere"
    with pytest.raises(ValidationError):
        Template.model_validate(t)


def test_condition_names_resolve_to_generic_checks():
    t = load_templates()["evening_out"]
    assert t.condition("home_by").check == "latest_end"
    assert t.condition("latest_end").name == "home_by"
    assert t.condition("diet").applies_to == ["meal", "dessert"]
