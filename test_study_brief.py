from datetime import date
from study_brief import (
    build_child_brief,
    description_lines,
    merge_duplicates,
    planning_assessments,
    resolve_week_date,
)


def test_resolve_swedish_iso_week():
    assert resolve_week_date("Prov onsdag v. 39", reference=date(2026, 9, 12)).isoformat() == "2026-09-23"


def test_planning_assessment_inherits_resources():
    planning = [{
        "id": "1",
        "title": "Spanish",
        "subjects": [5006],
        "topics": ["Prov onsdag v. 39"],
        "preparation_instructions": ["Repetera glosor"],
        "what_to_study": [],
        "weekly_goals": [],
        "resources": [{"title": "Glosor", "url": "https://example.com"}],
    }]
    items = planning_assessments(planning, reference=date(2026, 9, 12))
    assert len(items) == 1
    assert items[0]["date"] == "2026-09-23"
    assert items[0]["resources"][0]["title"] == "Glosor"


def test_merge_duplicate_test_records():
    items = [
        {"id":"1","kind":"test","date":"2026-09-11","title":"Prov: Medeltidens litteratur","subjects":["Svenska"],"source":{"source_id":1}},
        {"id":"2","kind":"test","date":"2026-09-11","title":"Svenska - prov: Medeltidens litteratur","subjects":["Svenska"],"source":{"source_id":2}},
    ]
    merged = merge_duplicates(items)
    assert len(merged) == 1
    assert len(merged[0]["sources"]) == 2


def test_description_lines_drops_bare_url_lines():
    text = "Prov i spanska på onsdag v. 39.\nhttps://example.com/glosor\nRepetera verben."
    assert description_lines(text) == ["Prov i spanska på onsdag v. 39.", "Repetera verben."]


def test_child_brief_what_to_study_fallback_excludes_urls():
    # Regression test: a test/quiz with no separately-derived guidance previously fell
    # back to dumping the entire raw description (including redundant URLs already shown
    # under Resources) as a single what_to_study bullet.
    child = {
        "pupil": {"name": "Family, Alex"},
        "normalized": {
            "study_items": [{
                "id": "1",
                "kind": "test",
                "priority": "high",
                "date": "2026-09-17",
                "title": "Prov",
                "description": "Läs kapitel 3.\nhttps://example.com/kapitel3\nÖva glosor.",
                "subjects": ["Svenska"],
                "what_to_study": [],
                "preparation_instructions": [],
                "weekly_goals": [],
                "topics": [],
                "resources": [{"title": "Kapitel 3", "url": "https://example.com/kapitel3"}],
                "source": {"type": "calendar", "source_id": 1},
            }],
            "important_dates": {},
            "planning": [],
        },
    }
    brief = build_child_brief(child, as_of=date(2026, 9, 12), days=14)
    item = brief["upcoming"][0]
    assert "https://example.com/kapitel3" not in item["what_to_study"]
    assert "Läs kapitel 3." in item["what_to_study"]
    assert "Öva glosor." in item["what_to_study"]


def test_merge_does_not_combine_distinct_same_day_tests():
    # Two genuinely different tests on the same day, in different subjects, must not be
    # collapsed into one just because they share a date/kind.
    items = [
        {"id":"1","kind":"test","date":"2026-09-11","title":"Matematik - prov: Bråk","subjects":["Matematik"],"source":{"source_id":1}},
        {"id":"2","kind":"test","date":"2026-09-11","title":"Engelska - prov: Grammar","subjects":["Engelska"],"source":{"source_id":2}},
    ]
    merged = merge_duplicates(items)
    assert len(merged) == 2
