"""抽取结果与现有档案的三方对比与合并。"""

import json
import re

SIMPLE_PATHS = [
    "name", "email", "phone", "city", "status", "github", "linkedin",
    "school", "highest_degree", "major", "graduation_date", "english_level",
    "location_preference", "resume_language", "notes",
]
LIST_PATHS = [
    "languages", "education", "experiences", "projects",
    "certifications", "awards", "career_goals", "target_sectors", "deal_breakers",
]
SKILL_PATHS = ["skills.strong", "skills.moderate", "skills.weak"]


def _norm(v):
    if v is None:
        return ""
    return re.sub(r"\s+", "", str(v))


def _get_path(profile, path):
    parts = path.split(".")
    cur = profile
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _identity(item):
    return "|".join(str(item.get(k, "")) for k in ("school", "company", "title", "name", "period") if isinstance(item, dict))


def build_merge_plan(current_profile, extracted, confidence):
    fills, updates, skipped = [], [], []

    for path in SIMPLE_PATHS:
        current = _get_path(current_profile, path)
        new = extracted.get(path)
        conf = confidence.get(path, "low")
        item = {
            "field_path": path,
            "current_value": current,
            "new_value": new,
            "confidence": conf,
            "source_text": "",
        }
        if new in (None, "") or conf == "low":
            skipped.append(item)
        elif current in (None, "") or _norm(current) == "":
            fills.append(item)
        elif _norm(current) != _norm(new):
            updates.append(item)

    for path in SKILL_PATHS:
        current = _get_path(current_profile, path) or []
        new = (extracted.get("skills") or {}).get(path.split(".")[1], []) if isinstance(extracted.get("skills"), dict) else []
        if not new:
            continue
        added = [s for s in new if s not in current]
        if added:
            fills.append({
                "field_path": path,
                "current_value": current,
                "new_value": added,
                "confidence": confidence.get(path, "medium"),
                "source_text": "",
                "append": True,
            })

    for path in LIST_PATHS:
        current = _get_path(current_profile, path) or []
        new = extracted.get(path) or []
        if not new:
            continue
        current_ids = {_identity(x): x for x in current if isinstance(x, dict)}
        plain_current = {str(x): x for x in current if not isinstance(x, dict)}
        added = []
        for item in new:
            if isinstance(item, dict):
                if _identity(item) not in current_ids:
                    added.append(item)
            elif str(item) not in plain_current:
                added.append(item)
        if added:
            fills.append({
                "field_path": path,
                "current_value": [],
                "new_value": added,
                "confidence": confidence.get(path, "medium"),
                "source_text": "",
                "append": True,
            })

    return {"fills": fills, "updates": updates, "skipped": skipped}


def apply_paths(current_profile, merge_plan, accepted_paths):
    accepted = set(accepted_paths)
    result = json.loads(json.dumps(current_profile))
    applied = 0
    valid = {item["field_path"] for item in merge_plan.get("fills", []) + merge_plan.get("updates", [])}

    for path in accepted:
        if path not in valid:
            continue
        item = next((x for x in merge_plan.get("fills", []) + merge_plan.get("updates", []) if x["field_path"] == path), None)
        if not item:
            continue
        if path in SIMPLE_PATHS:
            result[path] = item["new_value"]
            applied += 1
        elif path in SKILL_PATHS:
            key = path.split(".")[1]
            skills = result.setdefault("skills", {"strong": [], "moderate": [], "weak": []})
            if item.get("append"):
                merged = list(skills.get(key, []))
                for v in item["new_value"]:
                    if v not in merged:
                        merged.append(v)
                skills[key] = merged
            else:
                skills[key] = item["new_value"]
            applied += 1
        elif path in LIST_PATHS:
            current = result.get(path, [])
            if item.get("append"):
                for v in item["new_value"]:
                    if v not in current:
                        current.append(v)
                result[path] = current
            else:
                result[path] = item["new_value"]
            applied += 1
    return result, applied
