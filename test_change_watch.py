import copy
import unittest
from datetime import date

from change_watch import compare
from infomentor_extract import build_normalized


def payload(item):
    return {
        "extracted_at": "2026-09-12T10:00:00+02:00",
        "children": [{
            "pupil": {"name": "Family, Alex", "switch_id": "1"},
            "normalized": {"study_items": [item], "important_dates": {}},
        }],
    }


class ChangeWatchTests(unittest.TestCase):
    def base_item(self):
        return {
            "id": "123",
            "kind": "test",
            "priority": "high",
            "title": "Prov: Ideologier och partier",
            "date": "2026-09-17",
            "subjects": ["Samhällskunskap"],
            "description": "Läs del 1",
            "what_to_study": ["Del 1"],
            "preparation_instructions": [],
            "topics": [],
            "resources": [],
            "source": {"type": "calendar", "source_id": 123},
        }

    def test_date_change_is_critical(self):
        a = payload(self.base_item())
        b = copy.deepcopy(a)
        b["extracted_at"] = "2026-09-13T10:00:00+02:00"
        b["children"][0]["normalized"]["study_items"][0]["date"] = "2026-09-18"
        d = compare(a, b)
        changes = [x for x in d["changes"] if x["type"] == "date_changed"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["severity"], "critical")

    def test_instruction_and_resource_change(self):
        a = payload(self.base_item())
        b = copy.deepcopy(a)
        item = b["children"][0]["normalized"]["study_items"][0]
        item["what_to_study"].append("Del 2")
        item["resources"].append({"title": "Study guide", "url": "https://example.com/guide", "provider": "Example"})
        d = compare(a, b)
        types = [x["type"] for x in d["changes"]]
        self.assertIn("instructions_changed", types)
        self.assertIn("resource_added", types)

    def test_new_quiz_is_critical(self):
        a = payload(self.base_item())
        b = copy.deepcopy(a)
        b["children"][0]["normalized"]["study_items"].append({
            "id": "456", "kind": "quiz", "priority": "high", "title": "Läxförhör",
            "date": "2026-09-22", "subjects": ["NO"], "resources": [],
            "source": {"type": "calendar", "source_id": 456},
        })
        d = compare(a, b)
        ch = [x for x in d["changes"] if x["type"] == "new_item" and x["item"]["id"] == "456"]
        self.assertEqual(len(ch), 1)
        self.assertEqual(ch[0]["severity"], "critical")

    def test_same_item_unchanged(self):
        a = payload(self.base_item())
        b = copy.deepcopy(a)
        d = compare(a, b)
        self.assertEqual(d["change_count"], 0)

    def test_item_outside_new_window_is_not_reported_as_removed(self):
        # The default extraction window is only the current + next school week; an item
        # whose date now falls outside a re-extraction's requested range disappears purely
        # because of window sizing, not because anything changed at InfoMentor.
        a = payload(self.base_item())
        a["children"][0]["range"] = {"start": "2026-09-14", "end": "2026-09-27"}
        b = copy.deepcopy(a)
        b["children"][0]["normalized"]["study_items"] = []
        b["children"][0]["range"] = {"start": "2026-09-28", "end": "2026-10-11"}
        d = compare(a, b)
        removed = [x for x in d["changes"] if x["type"] == "removed_item"]
        self.assertEqual(len(removed), 0)

    def test_item_removed_within_window_is_still_reported(self):
        # An item whose date is still inside the current window disappearing is real
        # information (e.g. deleted by the teacher) and must still be reported.
        a = payload(self.base_item())
        a["children"][0]["range"] = {"start": "2026-09-14", "end": "2026-09-27"}
        b = copy.deepcopy(a)
        b["children"][0]["normalized"]["study_items"] = []
        b["children"][0]["range"] = {"start": "2026-09-14", "end": "2026-09-27"}
        d = compare(a, b)
        removed = [x for x in d["changes"] if x["type"] == "removed_item"]
        self.assertEqual(len(removed), 1)

    def test_became_overdue_detected(self):
        # base_item() starts with no is_overdue key (falsy); flipping it to True on an
        # otherwise-unchanged high-kind item must surface as its own change type.
        a = payload(self.base_item())
        b = copy.deepcopy(a)
        item = b["children"][0]["normalized"]["study_items"][0]
        item["is_overdue"] = True
        d = compare(a, b)
        changes = [x for x in d["changes"] if x["type"] == "became_overdue"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["severity"], "high")


def _planning_only_payload(*, task_present: bool) -> dict:
    tasks = []
    if task_present:
        tasks.append({
            "id": 9001,
            "status": "Due",
            "dueDate": "2026-09-23",
            "subject": "Ämne saknas",
            "title": "PROV I SPANSKA",
            "description": "Prov i spanska.",
        })
    planning = {
        "index": {"uols": [{"id": 500, "title": "Spanish unit"}], "subjects": []},
        "details": {
            "500": {
                "id": 500,
                "title": "Spanish unit",
                "sections": [{"type": "task", "tasks": tasks}],
            }
        },
    }
    normalized = build_normalized([], [], {}, {}, planning, reference=date(2026, 9, 12))
    return {
        "extracted_at": "2026-09-12T10:00:00+02:00",
        "children": [{
            "pupil": {"name": "Family, Alex", "switch_id": "1"},
            "normalized": normalized,
        }],
    }


def _planning_resource_payload(*, resources: list[dict]) -> dict:
    planning = {
        "index": {"uols": [{"id": 700, "title": "Matematik Alfa"}], "subjects": []},
        "details": {
            "700": {
                "id": 700,
                "title": "Matematik Alfa",
                "sections": [{"type": "resource", "resources": resources, "title": "Material"}],
            }
        },
    }
    normalized = build_normalized([], [], {}, {}, planning, reference=date(2026, 9, 12))
    return {
        "extracted_at": "2026-09-12T10:00:00+02:00",
        "children": [{
            "pupil": {"name": "Family, Alex", "switch_id": "1"},
            "normalized": normalized,
        }],
    }


class PlanningChangeDetectionTests(unittest.TestCase):
    """Regression tests for planning-only assessments/resources being invisible to
    change detection: derived_from_planning was previously never persisted into the
    normalized JSON that change_watch.py actually diffs, so a brand-new test that only
    existed in teacher planning text produced zero change records."""

    def test_new_planning_only_test_is_detected(self):
        previous = _planning_only_payload(task_present=False)
        current = _planning_only_payload(task_present=True)
        diff = compare(previous, current)
        self.assertGreater(diff["change_count"], 0)
        new_items = [c for c in diff["changes"] if c["type"] == "new_item" and c["item"]["id"] == "9001"]
        self.assertEqual(len(new_items), 1)
        self.assertEqual(new_items[0]["severity"], "critical")

    def test_new_planning_only_resource_is_detected(self):
        previous = _planning_resource_payload(resources=[])
        current = _planning_resource_payload(resources=[
            {"id": 1, "title": "Facit kapitel 1", "url": "/Resources/Resource/Download/1", "type": "Media", "fileType": "pdf"},
        ])
        diff = compare(previous, current)
        resource_changes = [c for c in diff["changes"] if c["type"] == "resource_added"]
        self.assertEqual(len(resource_changes), 1)


if __name__ == "__main__":
    unittest.main()
