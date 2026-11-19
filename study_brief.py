#!/usr/bin/env python3
"""Create an actionable family Study Brief from normalized InfoMentor JSON.

The brief is intentionally study-centric. It prioritizes assessments and deadlines,
keeps regular lessons in the background, links planning resources to the assessment
that planning describes, and preserves provenance for every derived item.

No network access is required; this works entirely from a previously extracted JSON.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from infomentor_extract import classify_planning_mention, resolve_week_date

KIND_RANK = {
    "test": 0,
    "exam": 0,
    "quiz": 1,
    "homework": 2,
    "assignment": 3,
    "weekly_goal": 4,
    "school_event": 5,
    "unknown": 9,
}

KIND_LABEL = {
    "test": "TEST",
    "exam": "EXAM",
    "quiz": "QUIZ",
    "homework": "HOMEWORK",
    "assignment": "ASSIGNMENT",
    "weekly_goal": "WEEKLY GOAL",
    "school_event": "SCHOOL EVENT",
    "unknown": "ITEM",
}

ICONS = {
    "test": "🔴",
    "exam": "🔴",
    "quiz": "🔴",
    "homework": "🟠",
    "assignment": "🟡",
    "weekly_goal": "🟡",
    "school_event": "🔵",
    "unknown": "⚪",
}

STOPWORDS = {
    "prov", "test", "exam", "läxförhör", "förhör", "quiz", "veckans", "mål",
    "läxa", "homework", "uppgift", "och", "på", "i", "för", "med", "den", "det",
    "en", "ett", "till", "av", "samt", "antal", "rätt", "svenska", "samhällsprov",
}


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-zåäö0-9]+", title.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def title_similarity(a: str, b: str) -> float:
    aa, bb = title_tokens(a), title_tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def overlap_subjects(a: Iterable[str], b: Iterable[str]) -> bool:
    aa = {normalize_space(x).lower() for x in a if normalize_space(x)}
    bb = {normalize_space(x).lower() for x in b if normalize_space(x)}
    return bool(aa & bb) if aa and bb else False


def extract_as_of(payload: dict[str, Any]) -> date:
    raw = payload.get("extracted_at")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return date.today()


def planning_assessments(planning: list[dict[str, Any]], *, reference: date) -> list[dict[str, Any]]:
    """Promote explicit dated assessments/deadlines found only in planning text."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for plan in planning:
        source_id = str(plan.get("id") or "")
        plan_title = normalize_space(plan.get("title"))
        lines: list[str] = []
        for key in ("topics", "preparation_instructions", "what_to_study", "weekly_goals"):
            lines.extend(normalize_space(x) for x in plan.get(key, []) if normalize_space(x))

        for line in lines:
            kind = classify_planning_mention(line)
            if not kind:
                continue
            resolved = resolve_week_date(line, reference=reference)
            # Undated historical mentions are useful context but unsafe to promote as deadlines.
            if resolved is None:
                continue
            key = (source_id, kind, resolved.isoformat())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "id": f"planning:{source_id}:{kind}:{resolved.isoformat()}",
                "kind": kind,
                "priority": "high" if kind in {"test", "quiz", "homework"} else "medium",
                "date": resolved.isoformat(),
                "title": line,
                "subjects": plan.get("subjects") or [],
                "description": plan_title,
                "what_to_study": plan.get("what_to_study") or [],
                "preparation_instructions": plan.get("preparation_instructions") or [],
                "weekly_goals": plan.get("weekly_goals") or [],
                "topics": plan.get("topics") or [],
                "resources": plan.get("resources") or [],
                "derived_from_planning": True,
                "source": {"type": "planning", "source_id": source_id},
            })
    return out


def assessment_items(child: dict[str, Any], *, reference: date) -> list[dict[str, Any]]:
    n = child.get("normalized") or {}
    items = [dict(x) for x in n.get("study_items", [])]

    # Add separately promoted embedded assessment mentions from important_dates, because
    # they may represent a future quiz distinct from the parent weekly-goal item.
    existing_ids = {str(x.get("id")) for x in items}
    for bucket in ("tests", "quizzes"):
        for x in (n.get("important_dates") or {}).get(bucket, []):
            xid = str(x.get("id"))
            if xid in existing_ids or not x.get("derived_from_description"):
                continue
            parent_id = xid.split(":", 1)[0]
            parent = next((p for p in items if str(p.get("id")) == parent_id), {})
            item = dict(x)
            item.update({
                "description": parent.get("description") or "",
                "what_to_study": parent.get("what_to_study") or [],
                "preparation_instructions": parent.get("preparation_instructions") or [],
                "weekly_goals": parent.get("weekly_goals") or [],
                "topics": parent.get("topics") or [],
                "resources": parent.get("resources") or [],
            })
            items.append(item)
            existing_ids.add(xid)

    # Planning units may also mention a future assessment in prose (e.g. "Prov onsdag
    # v.39"); skip a mention when the same planning unit already produced a study item
    # with the same kind/date via a fully-specified embedded task, to avoid showing the
    # same assessment twice under two different titles.
    covered: set[tuple[str, str, str]] = set()
    for x in items:
        src = x.get("source") or {}
        planning_id = src.get("planning_id") or (src.get("source_id") if src.get("type") == "planning" else None)
        if planning_id and x.get("date") and x.get("kind"):
            covered.add((str(planning_id), str(x.get("kind")), str(x.get("date"))))

    for mention in planning_assessments(n.get("planning") or [], reference=reference):
        msrc = mention.get("source") or {}
        key = (str(msrc.get("source_id") or ""), str(mention.get("kind")), str(mention.get("date")))
        if key in covered:
            continue
        items.append(mention)
    return items


def merge_duplicates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge obvious duplicate assessment records but preserve every source/provenance."""
    result: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda x: (str(x.get("date") or "9999"), KIND_RANK.get(x.get("kind"), 9))):
        d = parse_date(item.get("date"))
        matched: dict[str, Any] | None = None
        for existing in result:
            if parse_date(existing.get("date")) != d:
                continue
            if existing.get("kind") != item.get("kind"):
                continue
            sim = title_similarity(str(existing.get("title") or ""), str(item.get("title") or ""))
            subj = overlap_subjects(existing.get("subjects") or [], item.get("subjects") or [])
            if sim >= 0.38 or (sim >= 0.20 and subj):
                matched = existing
                break
        if matched is None:
            new = dict(item)
            src = item.get("source")
            new["sources"] = [src] if src else []
            result.append(new)
            continue

        matched["sources"].append(item.get("source"))
        # Prefer the more descriptive title and richer text fields.
        if len(str(item.get("title") or "")) > len(str(matched.get("title") or "")):
            matched["title"] = item.get("title")
        for key in ("description",):
            if len(str(item.get(key) or "")) > len(str(matched.get(key) or "")):
                matched[key] = item.get(key)
        for key in ("subjects", "what_to_study", "preparation_instructions", "weekly_goals", "topics", "resources"):
            combined: list[Any] = []
            seen: set[str] = set()
            for val in list(matched.get(key) or []) + list(item.get(key) or []):
                marker = json.dumps(val, ensure_ascii=False, sort_keys=True) if isinstance(val, (dict, list)) else str(val)
                if marker not in seen:
                    seen.add(marker)
                    combined.append(val)
            matched[key] = combined
    return result


def relevant_resources(item: dict[str, Any], child: dict[str, Any]) -> list[dict[str, Any]]:
    """Resources directly attached to an item, plus same-planning resources when applicable."""
    resources = list(item.get("resources") or [])
    source = item.get("source") or {}
    stype = source.get("type")
    # A planning-derived mention/task links back to its planning unit either via its own
    # source_id (unit-wide mentions) or via planning_id (a fully-specified embedded task).
    sid = str(source.get("source_id") or "") if stype == "planning" else str(source.get("planning_id") or "")
    if sid:
        for r in (child.get("normalized") or {}).get("resources", []):
            rs = r.get("source") or {}
            if rs.get("type") == "planning" and str(rs.get("source_id")) == sid:
                resources.append(r)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in resources:
        url = str(r.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            out.append(r)
    return out


def description_lines(text: Any) -> list[str]:
    """Split a raw description into line-like segments for use as a what_to_study
    fallback, dropping lines that are just a bare URL (already shown under Resources)
    instead of dumping the entire multi-paragraph description as one blob."""
    out: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = normalize_space(raw_line)
        if not line or re.fullmatch(r"(?:https?://|www\.)\S+", line, re.I):
            continue
        out.append(line)
    return out[:10]


def concise_planning_prep(lines: list[str]) -> list[str]:
    """Prefer explicit preparation advice over week-by-week historical lesson notes."""
    strong = ("provet", "inför", "repetera", "öva", "träna", "titta igenom", "börja öva", "glosboken", "classroom")
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        clean = normalize_space(line)
        low = clean.lower()
        if not clean or low in seen:
            continue
        if any(x in low for x in strong):
            seen.add(low)
            out.append(clean)
    return out[:10]


def finalize_item(item: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Apply the per-item enrichment/trimming shared by every consumer of assessment
    items (Study Brief, Calendar sync, ...): attach cross-referenced resources, trim
    noisy planning-derived prep text down to explicit advice, and fall back to a
    segmented description for tests/exams/quizzes with no separately-derived
    what_to_study. Consumers must call this rather than reading raw assessment items
    directly, or they'll show empty/untrimmed content."""
    item = dict(item)
    item["resources"] = relevant_resources(item, child)
    if item.get("derived_from_planning"):
        item["preparation_instructions"] = concise_planning_prep(item.get("preparation_instructions") or [])
        item["topics"] = [x for x in (item.get("topics") or []) if not re.match(r"(?i)^vecka\s+\d+", normalize_space(x))][:8]
    if item.get("kind") in {"test", "exam", "quiz"} and not item.get("what_to_study") and normalize_space(item.get("description")):
        item["what_to_study"] = description_lines(item.get("description"))
    return item


def build_child_brief(child: dict[str, Any], *, as_of: date, days: int) -> dict[str, Any]:
    end = as_of + timedelta(days=days)
    all_items = merge_duplicates(assessment_items(child, reference=as_of))
    upcoming: list[dict[str, Any]] = []
    past_due: list[dict[str, Any]] = []

    for item in all_items:
        d = parse_date(item.get("date"))
        if not d:
            continue
        item = finalize_item(item, child)
        item["days_until"] = (d - as_of).days
        if as_of <= d <= end:
            upcoming.append(item)
        elif d < as_of and item.get("is_overdue") is True:
            past_due.append(item)

    upcoming.sort(key=lambda x: (parse_date(x.get("date")) or date.max, KIND_RANK.get(x.get("kind"), 9), str(x.get("title"))))
    past_due.sort(key=lambda x: parse_date(x.get("date")) or date.min, reverse=True)

    # Keep ordinary schedule separate and only for the same visible horizon.
    lessons = []
    for lesson in (child.get("normalized") or {}).get("schedule", []):
        d = parse_date(lesson.get("start"))
        if d and as_of <= d <= end:
            lessons.append(lesson)

    return {
        "pupil": child.get("pupil") or {},
        "as_of": as_of.isoformat(),
        "through": end.isoformat(),
        "upcoming": upcoming,
        "past_due": past_due,
        "schedule": lessons,
    }


def build_brief(payload: dict[str, Any], *, as_of: date | None = None, days: int = 14) -> dict[str, Any]:
    as_of = as_of or extract_as_of(payload)
    return {
        "study_brief_version": 1,
        "generated_from": payload.get("source"),
        "source_extracted_at": payload.get("extracted_at"),
        "as_of": as_of.isoformat(),
        "days": days,
        "children": [build_child_brief(c, as_of=as_of, days=days) for c in payload.get("children", [])],
    }


def display_name(pupil: dict[str, Any]) -> str:
    raw = normalize_space(pupil.get("name"))
    if "," in raw:
        surname, given = raw.split(",", 1)
        return normalize_space(given) or raw
    return raw


def md_list(lines: list[str], prefix: str = "- ") -> list[str]:
    return [prefix + normalize_space(x) for x in lines if normalize_space(x)]


def render_markdown(brief: dict[str, Any]) -> str:
    out: list[str] = [
        "# Family Study Brief",
        "",
        f"As of **{brief['as_of']}** · next **{brief['days']} days**",
        "",
    ]
    for child in brief.get("children", []):
        name = display_name(child.get("pupil") or {})
        out.extend([f"## {name}", ""])
        upcoming = child.get("upcoming") or []
        if not upcoming:
            out.extend(["No upcoming study items found in the extracted horizon.", ""])
        for item in upcoming:
            kind = item.get("kind") or "unknown"
            icon = ICONS.get(kind, "⚪")
            label = KIND_LABEL.get(kind, kind.upper())
            date_s = str(item.get("date") or "")
            subjects = ", ".join(str(x) for x in item.get("subjects") or [] if str(x))
            title = normalize_space(item.get("title"))
            heading = f"### {icon} {date_s} — {label}: {title}"
            if subjects:
                heading += f" · {subjects}"
            out.extend([heading, ""])

            what = item.get("what_to_study") or []
            prep = item.get("preparation_instructions") or []
            topics = item.get("topics") or []
            resources = item.get("resources") or []

            if what:
                out.extend(["**What to study**", *md_list(what), ""])
            if prep:
                out.extend(["**Preparation**", *md_list(prep), ""])
            if topics:
                # Keep concise and avoid duplicating whole descriptions.
                concise = [x for x in topics if normalize_space(x) not in {normalize_space(y) for y in what + prep}][:10]
                if concise:
                    out.extend(["**Topics / teacher notes**", *md_list(concise), ""])
            if resources:
                out.append("**Resources**")
                for r in resources:
                    title_r = normalize_space(r.get("title")) or normalize_space(r.get("provider")) or "Resource"
                    provider = normalize_space(r.get("provider"))
                    if provider and provider.lower() not in title_r.lower():
                        title_r += f" ({provider})"
                    out.append(f"- [{title_r}]({r.get('url')})")
                out.append("")

            if item.get("derived_from_planning"):
                out.extend(["_Date discovered from teacher planning text; not a calendar event._", ""])
            elif item.get("derived_from_description"):
                out.extend(["_Date discovered inside another InfoMentor item's description._", ""])

        past = child.get("past_due") or []
        if past:
            out.extend(["### ⚠️ Past due / unresolved", ""])
            for item in past[:8]:
                out.append(f"- {item.get('date')} — {normalize_space(item.get('title'))}")
            out.append("")

        # Schedule is deliberately collapsed into a simple reference section.
        if child.get("schedule"):
            by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for lesson in child["schedule"]:
                d = str(lesson.get("start") or "")[:10]
                by_day[d].append(lesson)
            out.extend(["### Regular lessons (reference)", ""])
            for d in sorted(by_day):
                bits = []
                for lesson in sorted(by_day[d], key=lambda x: str(x.get("start") or "")):
                    start = str(lesson.get("start") or "")[11:16]
                    bits.append(f"{start} {normalize_space(lesson.get('subject'))}")
                out.append(f"- **{d}:** " + " · ".join(bits))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description="Build a study-first brief from InfoMentor normalized JSON")
    p.add_argument("input", help="InfoMentor v2/v3 JSON")
    p.add_argument("--as-of", type=date.fromisoformat, help="Reference date YYYY-MM-DD (default: extraction date)")
    p.add_argument("--days", type=int, default=28, help="Upcoming horizon in days (default: 28)")
    p.add_argument("--json-output", default="study-brief.json")
    p.add_argument("--markdown-output", default="study-brief.md")
    args = p.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    brief = build_brief(payload, as_of=args.as_of, days=args.days)
    Path(args.json_output).write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.markdown_output).write_text(render_markdown(brief), encoding="utf-8")
    print(f"Wrote {args.json_output}")
    print(f"Wrote {args.markdown_output}")


if __name__ == "__main__":
    main()
