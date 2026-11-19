import unittest
from datetime import date

from infomentor_extract import (
    build_normalized,
    classify_study_item,
    dedupe_resources,
    extract_assessment_mentions,
    extract_urls_from_text,
    normalize_lesson,
    normalize_subject_name,
    provider_for_url,
)


class NormalizationTests(unittest.TestCase):
    def test_exam_title(self):
        self.assertEqual(classify_study_item("Svenska - prov: Medeltiden")[0], "test")

    def test_calendar_type_id_shortcuts(self):
        # InfoMentor's own calendarEntryTypeId values (reverse-engineered, undocumented by
        # InfoMentor) short-circuit keyword classification. Locking these in as a regression
        # test so a future change to this mapping is deliberate, per CLAUDE.md's instruction
        # to add a fixture before changing classification rules.
        self.assertEqual(classify_study_item("Some title", type_id=17)[0], "test")
        self.assertEqual(classify_study_item("Some title", type_id=11)[0], "homework")
        self.assertEqual(classify_study_item("Some title", type_id=16)[0], "weekly_goal")
        self.assertEqual(classify_study_item("Some title", type_id=15)[0], "school_event")
        self.assertEqual(classify_study_item("Some title", type_id=3)[0], "assignment")

    def test_weekly_goal_not_promoted_by_future_quiz_text(self):
        kind, _ = classify_study_item(
            "Veckans mål NO",
            "Observera: Förhöret genomförs tisdagen den 22 september.",
            type_id=3,
        )
        self.assertEqual(kind, "weekly_goal")

    def test_explicit_swedish_date(self):
        mentions = extract_assessment_mentions(
            "Observera: Förhöret genomförs tisdagen den 22 september.",
            "2026-09-17",
        )
        self.assertEqual(mentions[0]["date"], "2026-09-22")

    def test_naked_url_uses_preceding_label(self):
        resources = extract_urls_from_text(
            '<p><strong>Träna på verb:</strong></p><p>https://example.com/a</p>'
        )
        self.assertEqual(resources[0]["title"], "Träna på verb:")

    def test_known_subject_abbreviations_normalized(self):
        self.assertEqual(normalize_subject_name("SV"), "Svenska")
        self.assertEqual(normalize_subject_name("ma"), "Matematik")
        self.assertEqual(normalize_subject_name("En"), "Engelska")

    def test_ambiguous_subject_abbreviation_not_guessed(self):
        # "NO" is context-dependent (naturorienterande ämnen vs. other readings); ROADMAP
        # explicitly calls this out as unsafe to normalize, so it must pass through as-is.
        self.assertEqual(normalize_subject_name("NO"), "NO")

    def test_normalize_lesson_preserves_raw_subject(self):
        lesson = normalize_lesson({"title": "SV", "start": "2026-09-14T08:00:00"})
        self.assertEqual(lesson["subject"], "Svenska")
        self.assertEqual(lesson["subject_raw"], "SV")

    def test_glosboken_subdomain_not_classified_as_generic_google(self):
        # google.glosboken.se is a Glosboken-hosted invite link; provider detection order
        # is load-bearing here (Glosboken must be checked before the generic "google" match).
        self.assertEqual(provider_for_url("https://google.glosboken.se/invitations/abc"), "Glosboken")

    def test_dedupe_resources_preserves_distinct_source_attribution(self):
        # The same URL can legitimately be attached to two different sources (e.g. a
        # calendar item and a planning unit); both copies must survive deduplication.
        resources = [
            {"url": "https://example.com/x", "title": "Calendar copy", "source": {"type": "calendar", "source_id": "1"}},
            {"url": "https://example.com/x", "title": "Planning copy", "source": {"type": "planning", "source_id": "2"}},
            {"url": "https://example.com/x", "title": "Calendar copy again", "source": {"type": "calendar", "source_id": "1"}},
        ]
        result = dedupe_resources(resources)
        self.assertEqual(len(result), 2)

    def test_planning_embedded_task_promoted_as_assessment(self):
        # Planning 'task' sections can contain a fully-specified future assessment (its
        # own id/title/dueDate) before it ever appears as a dedicated calendar entry.
        planning = {
            "index": {"uols": [{"id": 500, "title": "Spanish unit"}], "subjects": []},
            "details": {
                "500": {
                    "id": 500,
                    "title": "Spanish unit",
                    "sections": [
                        {
                            "type": "task",
                            "title": "Uppgifter",
                            "tasks": [
                                {
                                    "id": 9001,
                                    "status": "Due",
                                    "dueDate": "2026-09-23",
                                    "subject": "Ämne saknas",
                                    "title": "PROV I SPANSKA",
                                    "description": (
                                        "<p>Prov i spanska på onsdag v. 39.</p>"
                                        '<p><a href="https://example.com/glosor">Glosor</a></p>'
                                    ),
                                }
                            ],
                        }
                    ],
                }
            },
        }
        normalized = build_normalized([], [], {}, {}, planning, reference=date(2026, 9, 12))

        study = [x for x in normalized["study_items"] if x["id"] == "9001"]
        self.assertEqual(len(study), 1)
        self.assertEqual(study[0]["kind"], "test")
        self.assertTrue(study[0]["derived_from_planning"])
        self.assertEqual(study[0]["source"]["type"], "planning_task")
        self.assertEqual([r["url"] for r in study[0]["resources"]], ["https://example.com/glosor"])

        tests_bucket = normalized["important_dates"]["tests"]
        self.assertEqual([x["id"] for x in tests_bucket], ["9001"])

    def test_planning_task_already_in_calendar_is_not_duplicated(self):
        # A task fetched via the calendar/task pipeline that also appears embedded in a
        # planning 'task' section must not be represented twice.
        calendar = [{"id": 9001, "calendarEntryTypeId": 3, "title": "Prov", "startDate": "2026-09-23"}]
        tasks = {"9001": {"title": "PROV I SPANSKA", "description": "Prov."}}
        planning = {
            "index": {"uols": [{"id": 500, "title": "Spanish unit"}], "subjects": []},
            "details": {
                "500": {
                    "id": 500,
                    "title": "Spanish unit",
                    "sections": [
                        {
                            "type": "task",
                            "tasks": [{"id": 9001, "dueDate": "2026-09-23", "title": "PROV I SPANSKA"}],
                        }
                    ],
                }
            },
        }
        normalized = build_normalized([], calendar, tasks, {}, planning, reference=date(2026, 9, 12))
        matching = [x for x in normalized["study_items"] if x["id"] == "9001"]
        self.assertEqual(len(matching), 1)

    def test_assessment_groups_link_calendar_entry_and_planning_mention(self):
        # The same real-world test can show up both as its own calendar entry (id 111)
        # and as a separate planning-unit prose mention (a different, "planning:..." id).
        # These must be linked via canonical_id/assessment_groups, not merged/deleted.
        calendar = [
            {
                "id": 111,
                "calendarEntryTypeId": 17,
                "title": "Prov i matematik",
                "startDate": "2026-09-23",
                "subjects": [{"title": "Matematik"}],
            }
        ]
        planning = {
            "index": {"uols": [{"id": 700, "title": "Matte vecka 39", "subjects": [{"name": "Matematik"}]}], "subjects": []},
            "details": {
                "700": {
                    "id": 700,
                    "title": "Matte vecka 39",
                    "description": "Prov i matematik onsdag v.39",
                }
            },
        }
        normalized = build_normalized([], calendar, {}, {}, planning, reference=date(2026, 9, 12))

        tests_bucket = normalized["important_dates"]["tests"]
        self.assertEqual(len(tests_bucket), 2)
        canonical_ids = {entry["canonical_id"] for entry in tests_bucket}
        self.assertEqual(len(canonical_ids), 1)

        groups = normalized["assessment_groups"]
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["kind"], "test")
        self.assertEqual(group["date"], "2026-09-23")
        self.assertEqual(group["subjects"], ["Matematik"])
        self.assertEqual(len(group["sources"]), 2)
        self.assertEqual({s["collection"] for s in group["sources"]}, {"important_dates.tests"})

    def test_assessment_groups_do_not_merge_distinct_same_day_homework(self):
        # Two distinct homework items due the same day in the same subject must NOT be
        # linked just because kind/date/subject match -- title similarity is also required.
        calendar = [
            {
                "id": 201,
                "calendarEntryTypeId": 11,
                "title": "Läsläxa kapitel 3",
                "startDate": "2026-09-23",
                "subjects": [{"title": "Svenska"}],
            },
            {
                "id": 202,
                "calendarEntryTypeId": 11,
                "title": "Multiplikationstabellen",
                "startDate": "2026-09-23",
                "subjects": [{"title": "Svenska"}],
            },
        ]
        normalized = build_normalized([], calendar, {}, {}, {}, reference=date(2026, 9, 12))

        homework_bucket = normalized["important_dates"]["homework"]
        self.assertEqual(len(homework_bucket), 2)
        canonical_ids = {entry["canonical_id"] for entry in homework_bucket}
        self.assertEqual(len(canonical_ids), 2, "distinct homework titles must not share a canonical_id")
        self.assertEqual(normalized["assessment_groups"], [])


if __name__ == "__main__":
    unittest.main()
