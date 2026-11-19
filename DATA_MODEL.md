# Data Model

## Goals

The normalized data model exists to hide InfoMentor-specific implementation details from downstream consumers.

The model should answer:

- What happens at school?
- What requires preparation?
- When is it due?
- What should the child study?
- Which resources should they use?
- Where did this information come from?
- Was the information explicit or derived?

Versioning: the normalized extraction (`out/infomentor-current.json`) carries
`schema_version: 3`; the Study Brief carries `study_brief_version: 1`. Note that Study
Brief fields like `days_until`/`is_overdue` are computed at generation time relative to
`as_of` — consumers showing them later should recompute from the item date.

---

## Child

Each child is discovered dynamically.

Conceptual shape:

```json
{
  "pupil": {
    "name": "...",
    "switch_id": "...",
    "switch_url": "..."
  },
  "normalized": {
    "schedule": [],
    "important_dates": {},
    "study_items": [],
    "resources": [],
    "planning": [],
    "notifications": []
  },
  "raw": {}
}
```

Do not hard-code pupil identity.

---

## Schedule

Regular timetable items.

```json
{
  "start": "2026-09-18T08:30:00",
  "end": "2026-09-18T09:20:00",
  "subject": "Svenska",
  "subject_raw": "SV",
  "room": "Mitt 2",
  "teachers": "...",
  "notes": "",
  "all_day": false,
  "source": {
    "type": "timetable"
  }
}
```

`subject` is normalized where a known, unambiguous abbreviation mapping exists (e.g.
MA→Matematik, SV→Svenska, EN→Engelska); ambiguous abbreviations (e.g. NO) are left
unchanged rather than guessed. `subject_raw` always preserves InfoMentor's own raw value
(principle 1: preserve source truth), regardless of whether `subject` was normalized.

Schedule items carry no id from InfoMentor; a lesson's own start time is treated as its
stable identity by consumers that need one (e.g. Calendar sync).

Schedule items are background context, not automatically high-priority items.

---

## Important dates

Grouped by meaning rather than InfoMentor type ID.

Recommended groups:

```text
tests
quizzes
homework
assignments
school_events
other
```

Example:

```json
{
  "id": "100000001",
  "kind": "test",
  "priority": "high",
  "date": "2026-09-17",
  "title": "Samhällsprov",
  "subjects": ["Samhällskunskap"],
  "source": {
    "type": "calendar",
    "source_id": 100000001,
    "infomentor_type": 17,
    "url": null
  }
}
```

Derived assessment dates should include a flag such as:

```json
{
  "derived_from_description": true
}
```

---

## Study item

Study items are the main actionable objects.

```json
{
  "id": "...",
  "kind": "test",
  "priority": "high",
  "subjects": ["Svenska"],
  "title": "...",
  "date": "2026-09-23",
  "description": "...",
  "status": "Due",
  "is_overdue": false,
  "what_to_study": [],
  "preparation_instructions": [],
  "weekly_goals": [],
  "topics": [],
  "assessment_mentions": [],
  "resources": [],
  "source": {}
}
```

### `what_to_study`

Explicit teacher guidance about knowledge/content to learn.

Examples:
- textbook pages
- named topics
- concepts that must be understood
- required vocabulary

### `preparation_instructions`

Actions the student should perform.

Examples:
- work with study questions
- practice verb conjugation
- rehearse vocabulary
- complete specified exercises
- review previous homework

### `weekly_goals`

Teacher-defined weekly learning goals.

Weekly goals are not automatically tests or deadlines.

### `topics`

Useful supporting content that does not cleanly fit the above categories.

Avoid treating generic encouragement or unrelated prose as topics.

---

## Assessment mentions

Teacher descriptions may mention assessments that differ from the parent item's own date.

Example:

```json
{
  "kind": "quiz",
  "date": "2026-09-22",
  "text": "Förhöret ... genomförs tisdagen den 22 september.",
  "derived": true
}
```

This information may later produce a separate derived important-date entry.

Do not overwrite the source item's own due date.

---

## Resources

Resources should be normalized independently.

Example link:

```json
{
  "type": "link",
  "provider": "Glosboken",
  "title": "Träna på hur ar-verb böjs",
  "url": "https://...",
  "source": {
    "type": "planning",
    "source_id": "..."
  }
}
```

Example InfoMentor file:

```json
{
  "type": "file",
  "provider": "InfoMentor",
  "title": "Veckans matte kapitel 1 Alfa",
  "format": "pdf",
  "url": "/Resources/Resource/Download/...",
  "source": {
    "type": "planning",
    "source_id": "..."
  }
}
```

Do not assume InfoMentor's `hasLinks`/`hasFiles` flags are complete.

Scan content itself.

---

## Planning

Planning units are long-lived learning-context objects.

Conceptual shape:

```json
{
  "id": "...",
  "title": "...",
  "start": null,
  "end": null,
  "subjects": [],
  "has_assessments": true,
  "what_to_study": [],
  "preparation_instructions": [],
  "weekly_goals": [],
  "topics": [],
  "assessment_mentions": [],
  "resources": [],
  "source": {}
}
```

Planning may contain:
- study instructions
- future assessments
- learning objectives
- downloadable material
- external links

Planning-derived assessment discovery is a first-class feature, with two distinct mechanisms:

1. **Embedded tasks.** A planning unit's `sections[]` may include a `type: "task"` section
   whose `tasks[]` are fully-specified assessments (their own id/title/dueDate/description) that
   have not yet appeared as a calendar entry. These are promoted into `study_items` and
   `important_dates` with `source.type = "planning_task"` and `source.planning_id` pointing back
   at the owning planning unit, and are marked `derived_from_planning: true`. A task id already
   present in the calendar/task extraction is not duplicated here.
2. **Prose mentions.** Guidance text (e.g. "Prov onsdag v.39") that is not tied to a specific
   embedded task is resolved conservatively (explicit weekday + ISO week only) and kept on the
   planning unit as `assessment_mentions`, then promoted into `important_dates` with
   `source.type = "planning"` and `derived_from_planning: true`. A mention already covered by an
   embedded task's own kind/date is not promoted a second time.

---

## Provenance

Every derived or normalized object should retain enough information to trace it back.

Minimum source metadata should include where applicable:

```json
{
  "source": {
    "type": "calendar|task|planning|planning_task|timetable|notification",
    "source_id": "...",
    "infomentor_type": null,
    "url": null
  }
}
```

`planning_task` additionally carries `planning_id`, pointing back at the owning planning unit
(used to also surface that unit's own resources as relevant to the task).

Derived fields should be clearly marked.

---

## Deduplication philosophy

Do not destructively deduplicate based only on:
- same date
- similar title
- same subject

A test may legitimately appear as:
- a calendar event
- a task
- a planning assessment

Preferred future model:

```text
canonical assessment
├── source A
├── source B
└── source C
```

Until canonical linking is reliable, preserve all source records.

### Canonical assessment linking (implemented)

`important_dates` entries in the `tests`, `quizzes`, `homework` and `assignments` buckets
(and their corresponding `study_items` entry) carry a `canonical_id` field. Records only
share a `canonical_id` when they match on *all* of: same `kind`, same `date`, same
normalized subject set, **and** clearly overlapping titles (>=50% word overlap) — a single
signal alone (e.g. same date + same subject) is not sufficient, since distinct homework
items routinely share a day and subject.

`normalized.assessment_groups` lists only groups with more than one linked record:

```json
{
  "canonical_id": "canon:test:2026-09-23:matematik:111",
  "kind": "test",
  "date": "2026-09-23",
  "subjects": ["Matematik"],
  "titles": ["Prov i matematik", "Prov i matematik onsdag v.39"],
  "sources": [
    {"collection": "important_dates.tests", "id": "111", "source": {"type": "calendar", "...": "..."}},
    {"collection": "important_dates.tests", "id": "planning:700:test:2026-09-23", "source": {"type": "planning", "...": "..."}}
  ]
}
```

No source record is ever merged, altered or dropped by this process — `canonical_id` is
purely additive. `school_events` and `other` are excluded from linking since same-day
events are common without being duplicates.

---

## Privacy

Normalized output is still private child data.

Do not:
- commit it
- use real data as public fixtures
- expose it through logs unnecessarily
- store credentials inside normalized objects
