#!/usr/bin/env python3
"""Stateful change detector for normalized InfoMentor extractions.

Usage patterns:

1) Explicit diff:
   python change_watch.py previous.json current.json --json-output changes.json --markdown-output changes.md

2) Stateful watch/baseline:
   python change_watch.py current.json --state-dir .infomentor-state

The first stateful run stores a baseline and reports no changes. Later runs compare
against the previous snapshot, emit only meaningful school/study changes, then update
the baseline. No network access is required.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

HIGH_KINDS = {"test", "exam", "quiz"}
MEDIUM_KINDS = {"homework", "assignment", "weekly_goal", "planning"}

CHANGE_ORDER = {
    "date_changed": 0,
    "new_item": 1,
    "became_overdue": 2,
    "instructions_changed": 3,
    "resource_added": 4,
    "resource_removed": 5,
    "title_changed": 6,
    "priority_changed": 7,
    "removed_item": 8,
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}

STOPWORDS = {
    "prov", "test", "exam", "läxförhör", "förhör", "quiz", "veckans", "mål",
    "läxa", "homework", "uppgift", "och", "på", "i", "för", "med", "den", "det",
    "en", "ett", "till", "av", "samt", "antal", "rätt", "vecka", "v",
}


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm_lower(value: Any) -> str:
    return norm(value).casefold()


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def title_tokens(value: str) -> set[str]:
    # Strip dates/week numbers so a moved assessment can still match itself.
    s = value.casefold()
    s = re.sub(r"\b20\d{2}-\d{1,2}-\d{1,2}\b", " ", s)
    s = re.sub(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", " ", s)
    s = re.sub(r"\bv(?:ecka)?\.?\s*\d{1,2}\b", " ", s)
    words = re.findall(r"[a-zåäö0-9]+", s)
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def similarity(a: str, b: str) -> float:
    aa, bb = title_tokens(a), title_tokens(b)
    if not aa or not bb:
        return 1.0 if norm_lower(a) == norm_lower(b) and norm(a) else 0.0
    return len(aa & bb) / len(aa | bb)


def source_tuple(item: dict[str, Any]) -> tuple[str, str]:
    src = item.get("source") or {}
    return (str(src.get("type") or ""), str(src.get("source_id") or ""))


def stable_id(item: dict[str, Any]) -> str:
    """Stable identity independent of dates where possible."""
    src_type, src_id = source_tuple(item)
    kind = str(item.get("kind") or "unknown")
    if src_id:
        # Calendar/task IDs are stable. Planning IDs can emit multiple assessments,
        # so include a title token signature for derived planning items.
        if src_type == "planning" or item.get("derived_from_planning"):
            sig = ",".join(sorted(title_tokens(str(item.get("title") or ""))))[:160]
            return f"{src_type}:{src_id}:{kind}:{sig}"
        return f"{src_type}:{src_id}:{kind}"
    iid = str(item.get("id") or "")
    if iid:
        # Strip common generated suffix :quiz:YYYY-MM-DD etc.
        iid = re.sub(r":(?:test|exam|quiz|homework):\d{4}-\d{2}-\d{2}$", "", iid)
        return f"id:{iid}:{kind}"
    raw = f"{kind}|{norm_lower(item.get('title'))}|{','.join(sorted(map(str, item.get('subjects') or [])))}"
    return "hash:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def child_key(child: dict[str, Any]) -> str:
    pupil = child.get("pupil") or {}
    return str(pupil.get("switch_id") or pupil.get("name") or "unknown")


def child_name(child: dict[str, Any]) -> str:
    pupil = child.get("pupil") or {}
    name = norm(pupil.get("name"))
    if "," in name:
        # "Surname, First" -> "First"
        return name.split(",", 1)[1].strip() or name
    return name or "Child"


def extract_items(child: dict[str, Any]) -> list[dict[str, Any]]:
    n = child.get("normalized") or {}
    items = [copy.deepcopy(x) for x in n.get("study_items", [])]

    # Include promoted derived important dates (e.g. an embedded future quiz, or an
    # assessment mentioned only in planning prose) even when they are not separate
    # study_items.
    existing_ids = {str(x.get("id")) for x in items}
    imp = n.get("important_dates") or {}
    for bucket in ("tests", "quizzes", "homework", "assignments", "school_events", "other"):
        for x in imp.get(bucket, []) or []:
            xid = str(x.get("id") or "")
            if xid and xid not in existing_ids and (x.get("derived_from_description") or x.get("derived_from_planning")):
                items.append(copy.deepcopy(x))
                existing_ids.add(xid)

    # Planning units themselves are diffable: a new/changed resource or guidance line can
    # appear on a unit with no detected assessment mention at all (e.g. a new worksheet
    # attached to an ongoing topic), which would otherwise be invisible to change detection.
    for plan in n.get("planning") or []:
        pid = str(plan.get("id") or "")
        if not pid:
            continue
        items.append({
            "id": f"planning-unit:{pid}",
            "kind": "planning",
            "priority": "medium",
            "date": plan.get("end") or plan.get("start"),
            "title": plan.get("title"),
            "subjects": plan.get("subjects") or [],
            "description": "",
            "what_to_study": plan.get("what_to_study") or [],
            "preparation_instructions": plan.get("preparation_instructions") or [],
            "weekly_goals": plan.get("weekly_goals") or [],
            "topics": plan.get("topics") or [],
            "resources": plan.get("resources") or [],
            "source": plan.get("source") or {"type": "planning", "source_id": pid},
        })
    return items


def flatten_text(item: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in ("description",):
        if norm(item.get(key)):
            pieces.append(norm(item.get(key)))
    for key in ("what_to_study", "preparation_instructions", "weekly_goals", "topics"):
        pieces.extend(norm(x) for x in (item.get(key) or []) if norm(x))
    # Preserve order but dedupe.
    seen: set[str] = set()
    out: list[str] = []
    for p in pieces:
        k = p.casefold()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return "\n".join(out)


def normalized_resources(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in item.get("resources") or []:
        if not isinstance(r, dict):
            continue
        url = norm(r.get("url"))
        title = norm(r.get("title"))
        key = url or f"{norm_lower(r.get('provider'))}|{norm_lower(title)}"
        if key:
            out[key] = r
    return out


def child_range(child: dict[str, Any]) -> tuple[date | None, date | None]:
    r = (child or {}).get("range") or {}
    return parse_date(r.get("start")), parse_date(r.get("end"))


def is_outside_range(item_date: date | None, bounds: tuple[date | None, date | None]) -> bool:
    """True if item_date falls outside a [start, end] extraction window (either bound may be absent)."""
    start, end = bounds
    if item_date is None:
        return False
    if start and item_date < start:
        return True
    if end and item_date > end:
        return True
    return False


def severity(change_type: str, item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "unknown")
    if change_type == "date_changed" and kind in HIGH_KINDS:
        return "critical"
    if change_type == "new_item" and kind in HIGH_KINDS:
        return "critical"
    if kind in HIGH_KINDS:
        return "high"
    if kind in MEDIUM_KINDS:
        return "medium"
    return "info"


def item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "priority": item.get("priority"),
        "date": item.get("date"),
        "title": item.get("title"),
        "subjects": item.get("subjects") or [],
        "source": item.get("source") or {},
    }


def change_record(change_type: str, child: str, item: dict[str, Any], **extra: Any) -> dict[str, Any]:
    rec = {
        "type": change_type,
        "severity": severity(change_type, item),
        "child": child,
        "item": item_summary(item),
    }
    rec.update(extra)
    return rec


def match_items(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Match previous/current items, using stable source identity then cautious fuzzy fallback."""
    prev_by_id: dict[str, list[dict[str, Any]]] = {}
    cur_by_id: dict[str, list[dict[str, Any]]] = {}
    for x in previous:
        prev_by_id.setdefault(stable_id(x), []).append(x)
    for x in current:
        cur_by_id.setdefault(stable_id(x), []).append(x)

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used_prev: set[int] = set()
    used_cur: set[int] = set()

    for sid in set(prev_by_id) & set(cur_by_id):
        for p, c in zip(prev_by_id[sid], cur_by_id[sid]):
            pairs.append((p, c))
            used_prev.add(id(p))
            used_cur.add(id(c))

    # Fuzzy fallback catches planning-derived items whose generated title/date changed.
    for p in previous:
        if id(p) in used_prev:
            continue
        best: tuple[float, dict[str, Any]] | None = None
        psrc = source_tuple(p)
        for c in current:
            if id(c) in used_cur or p.get("kind") != c.get("kind"):
                continue
            csrc = source_tuple(c)
            if psrc[1] and csrc[1] and psrc != csrc:
                continue
            sim = similarity(str(p.get("title") or ""), str(c.get("title") or ""))
            subjects_overlap = bool(set(map(str, p.get("subjects") or [])) & set(map(str, c.get("subjects") or [])))
            score = sim + (0.15 if subjects_overlap else 0.0)
            if score >= 0.45 and (best is None or score > best[0]):
                best = (score, c)
        if best:
            c = best[1]
            pairs.append((p, c))
            used_prev.add(id(p))
            used_cur.add(id(c))

    removed = [p for p in previous if id(p) not in used_prev]
    added = [c for c in current if id(c) not in used_cur]
    return pairs, removed, added


def compare_child(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    name = child_name(current or previous)
    pitems = extract_items(previous)
    citems = extract_items(current)
    pairs, removed, added = match_items(pitems, citems)
    changes: list[dict[str, Any]] = []
    current_bounds = child_range(current)

    for p, c in pairs:
        pd, cd = parse_date(p.get("date")), parse_date(c.get("date"))
        if pd != cd:
            changes.append(change_record(
                "date_changed", name, c,
                before={"date": p.get("date")}, after={"date": c.get("date")},
            ))
        if not p.get("is_overdue") and c.get("is_overdue"):
            changes.append(change_record("became_overdue", name, c))
        if norm(p.get("title")) != norm(c.get("title")):
            # Don't emit trivial title changes if only date/week number text changed.
            if similarity(str(p.get("title") or ""), str(c.get("title") or "")) < 0.98:
                changes.append(change_record(
                    "title_changed", name, c,
                    before={"title": p.get("title")}, after={"title": c.get("title")},
                ))
        if str(p.get("priority") or "") != str(c.get("priority") or ""):
            changes.append(change_record(
                "priority_changed", name, c,
                before={"priority": p.get("priority")}, after={"priority": c.get("priority")},
            ))

        pt, ct = flatten_text(p), flatten_text(c)
        if pt != ct:
            changes.append(change_record(
                "instructions_changed", name, c,
                before={"study_text": pt}, after={"study_text": ct},
            ))

        pr, cr = normalized_resources(p), normalized_resources(c)
        for key in sorted(set(cr) - set(pr)):
            changes.append(change_record("resource_added", name, c, resource=cr[key]))
        for key in sorted(set(pr) - set(cr)):
            changes.append(change_record("resource_removed", name, c, resource=pr[key]))

    for c in added:
        changes.append(change_record("new_item", name, c))
        for r in normalized_resources(c).values():
            changes.append(change_record("resource_added", name, c, resource=r, part_of_new_item=True))
    for p in removed:
        # An item whose own date now falls outside the current extraction's requested
        # window disappears purely as an artifact of window sizing (default: current +
        # next school week), not because anything actually changed at InfoMentor.
        if is_outside_range(parse_date(p.get("date")), current_bounds):
            continue
        changes.append(change_record("removed_item", name, p))

    return changes


def compare(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    prev_children = {child_key(c): c for c in previous.get("children", [])}
    cur_children = {child_key(c): c for c in current.get("children", [])}
    all_keys = sorted(set(prev_children) | set(cur_children))

    changes: list[dict[str, Any]] = []
    for key in all_keys:
        p = prev_children.get(key, {"pupil": (cur_children.get(key) or {}).get("pupil", {}), "normalized": {}})
        c = cur_children.get(key, {"pupil": (prev_children.get(key) or {}).get("pupil", {}), "normalized": {}})
        changes.extend(compare_child(p, c))

    changes.sort(key=lambda x: (
        SEVERITY_ORDER.get(x.get("severity"), 9),
        str((x.get("item") or {}).get("date") or "9999"),
        CHANGE_ORDER.get(x.get("type"), 99),
        str(x.get("child") or ""),
    ))

    counts: dict[str, int] = {}
    for c in changes:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
    severities: dict[str, int] = {}
    for c in changes:
        severities[c["severity"]] = severities.get(c["severity"], 0) + 1

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "previous_extracted_at": previous.get("extracted_at"),
        "current_extracted_at": current.get("extracted_at"),
        "change_count": len(changes),
        "counts": counts,
        "severities": severities,
        "changes": changes,
    }


def resource_label(r: dict[str, Any]) -> str:
    provider = norm(r.get("provider"))
    title = norm(r.get("title")) or norm(r.get("url"))
    return f"{title} ({provider})" if provider else title


def to_markdown(diff: dict[str, Any]) -> str:
    lines = ["# InfoMentor changes", ""]
    lines.append(f"Compared: `{diff.get('previous_extracted_at') or 'baseline'}` → `{diff.get('current_extracted_at') or 'current'}`")
    lines.append("")
    if not diff.get("changes"):
        lines.extend(["No meaningful study changes detected.", ""])
        return "\n".join(lines)

    by_child: dict[str, list[dict[str, Any]]] = {}
    for ch in diff["changes"]:
        by_child.setdefault(ch["child"], []).append(ch)

    labels = {
        "date_changed": "📅 Date changed",
        "new_item": "🆕 New item",
        "became_overdue": "⏰ Now overdue",
        "instructions_changed": "📝 Study instructions changed",
        "resource_added": "🔗 Resource added",
        "resource_removed": "➖ Resource removed",
        "title_changed": "✏️ Title changed",
        "priority_changed": "⚠️ Priority changed",
        "removed_item": "✅ Removed / no longer present",
    }
    severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "info": "⚪"}

    for child, changes in by_child.items():
        lines.extend([f"## {child}", ""])
        for ch in changes:
            item = ch.get("item") or {}
            title = norm(item.get("title")) or "Untitled"
            d = norm(item.get("date"))
            icon = severity_icon.get(ch.get("severity"), "⚪")
            lines.append(f"### {icon} {labels.get(ch['type'], ch['type'])}: {title}")
            meta = [x for x in [str(item.get("kind") or ""), d] if x]
            if meta:
                lines.append(" · ".join(meta))
            if ch["type"] == "date_changed":
                lines.append(f"- **Before:** {ch.get('before', {}).get('date') or '—'}")
                lines.append(f"- **Now:** {ch.get('after', {}).get('date') or '—'}")
            elif ch["type"] == "resource_added":
                lines.append(f"- {resource_label(ch.get('resource') or {})}")
                if (ch.get("resource") or {}).get("url"):
                    lines.append(f"- {ch['resource']['url']}")
            elif ch["type"] == "resource_removed":
                lines.append(f"- {resource_label(ch.get('resource') or {})}")
            elif ch["type"] == "instructions_changed":
                before = norm((ch.get("before") or {}).get("study_text"))
                after = norm((ch.get("after") or {}).get("study_text"))
                if before:
                    lines.append(f"- **Previous:** {before[:500]}{'…' if len(before) > 500 else ''}")
                if after:
                    lines.append(f"- **Now:** {after[:700]}{'…' if len(after) > 700 else ''}")
            elif ch["type"] == "title_changed":
                lines.append(f"- **Before:** {ch.get('before', {}).get('title') or '—'}")
                lines.append(f"- **Now:** {ch.get('after', {}).get('title') or '—'}")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect meaningful changes between InfoMentor normalized extractions")
    ap.add_argument("files", nargs="+", type=Path, help="CURRENT.json (stateful) or PREVIOUS.json CURRENT.json")
    ap.add_argument("--state-dir", type=Path, default=Path(".infomentor-state"), help="State directory for one-file mode")
    ap.add_argument("--json-output", type=Path, default=Path("changes.json"))
    ap.add_argument("--markdown-output", type=Path, default=Path("changes.md"))
    ap.add_argument("--no-update-state", action="store_true", help="Do not replace state baseline after comparing")
    args = ap.parse_args()

    if len(args.files) not in (1, 2):
        ap.error("provide CURRENT.json for stateful mode, or PREVIOUS.json CURRENT.json for explicit diff")

    if len(args.files) == 2:
        previous_path, current_path = args.files
        previous, current = load_json(previous_path), load_json(current_path)
        diff = compare(previous, current)
    else:
        current_path = args.files[0]
        current = load_json(current_path)
        args.state_dir.mkdir(parents=True, exist_ok=True)
        baseline_path = args.state_dir / "latest.json"
        if not baseline_path.exists():
            shutil.copy2(current_path, baseline_path)
            diff = {
                "generated_at": datetime.now().astimezone().isoformat(),
                "previous_extracted_at": None,
                "current_extracted_at": current.get("extracted_at"),
                "change_count": 0,
                "counts": {},
                "severities": {},
                "changes": [],
                "baseline_created": True,
            }
        else:
            previous = load_json(baseline_path)
            diff = compare(previous, current)
            if not args.no_update_state:
                shutil.copy2(current_path, baseline_path)

    dump_json(args.json_output, diff)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(to_markdown(diff), encoding="utf-8")

    if diff.get("baseline_created"):
        print(f"Baseline created at {args.state_dir / 'latest.json'}")
    else:
        print(f"Detected {diff.get('change_count', 0)} meaningful change(s)")
    print(f"JSON: {args.json_output}")
    print(f"Markdown: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
