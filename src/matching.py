"""Deterministic matching of Drive videos to parsed Gemini content.

Rules (applied in this order, first hit wins):
1. Manual mapping chosen by the user (always authoritative).
2. Exact filename (case-insensitive)                     -> Matched
3. Drive file ID used as the content key                 -> Matched
4. Same filename ignoring extension                      -> Matched (if exactly one candidate)
5. Similar filename (only letters/digits compared)       -> Needs confirmation
6. "Video N" content and a filename ending in N          -> Needs confirmation
7. Otherwise                                             -> Unmatched

Ambiguity is never resolved automatically: several candidates, duplicate Drive
filenames or one content block claimed by several videos all require the user
to confirm, correct or skip. Matched content is deep-copied verbatim – the
parsed text is never rewritten.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from dataclasses import dataclass, field

from src.models import MatchStatus, Platform, PlatformContent, VideoRecord

NO_CONTENT = ""  # manual override meaning "this video has no content"


def stem(name: str) -> str:
    name = (name or "").strip().lower()
    return name.rsplit(".", 1)[0] if "." in name else name


def loose(name: str) -> str:
    """Letters/digits only (keeps Devanagari) – used for 'similar' suggestions."""
    return re.sub(r"[^a-z0-9\u0900-\u097f]", "", stem(name))


def _trailing_number(name: str) -> int | None:
    m = re.search(r"(\d+)\D*$", stem(name))
    return int(m.group(1)) if m else None


def confirm_token(video_id: str, content_key: str) -> str:
    return f"{video_id}::{content_key}"


@dataclass
class Match:
    video_id: str
    filename: str
    content_key: str | None
    method: str
    status: MatchStatus
    candidates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duplicate_with: list[str] = field(default_factory=list)


@dataclass
class MatchReport:
    matches: list[Match]
    unused_content: list[str]
    duplicate_filenames: list[str]

    def by_video(self) -> dict[str, Match]:
        return {m.video_id: m for m in self.matches}

    @property
    def needs_attention(self) -> list[Match]:
        return [m for m in self.matches
                if m.status in (MatchStatus.NEEDS_CONFIRMATION, MatchStatus.UNMATCHED)
                or m.duplicate_with]


def match_videos(
    videos: list[dict],
    content_keys: list[str],
    overrides: dict[str, str] | None = None,
    confirmed: set[str] | None = None,
    skipped: set[str] | None = None,
) -> MatchReport:
    overrides, confirmed, skipped = overrides or {}, confirmed or set(), skipped or set()
    keys = [k for k in content_keys if k != "(unassigned)"]
    by_exact = defaultdict(list)
    by_stem = defaultdict(list)
    by_loose = defaultdict(list)
    for k in keys:
        by_exact[k.strip().lower()].append(k)
        by_stem[stem(k)].append(k)
        by_loose[loose(k)].append(k)
    numbered = {int(m.group(1)): k for k in keys if (m := re.fullmatch(r"Video (\d+)", k))}
    name_counts = defaultdict(int)
    for v in videos:
        name_counts[v["name"].strip().lower()] += 1
    dup_names = sorted({v["name"] for v in videos if name_counts[v["name"].strip().lower()] > 1})

    matches: list[Match] = []
    for v in videos:
        vid, name = v["id"], v["name"]
        m = _auto_match(vid, name, keys, by_exact, by_stem, by_loose, numbered)
        if vid in overrides:
            key = overrides[vid] or None
            m = Match(vid, name, key, "Manual", MatchStatus.MATCHED if key else MatchStatus.UNMATCHED,
                      notes=["Mapping set manually."] if key else ["Marked as having no content."])
        else:
            if name_counts[name.strip().lower()] > 1 and m.status == MatchStatus.MATCHED:
                m.status = MatchStatus.NEEDS_CONFIRMATION
                m.notes.append("Another Drive video has the same filename – confirm the mapping.")
            if (m.status == MatchStatus.NEEDS_CONFIRMATION and m.content_key
                    and confirm_token(vid, m.content_key) in confirmed):
                m.status, m.method = MatchStatus.MATCHED, f"{m.method} (confirmed)"
        if vid in skipped:
            m.status = MatchStatus.SKIPPED
        matches.append(m)

    # one content block claimed by several (non-skipped) videos = duplicate records
    claims = defaultdict(list)
    for m in matches:
        if m.content_key and m.status != MatchStatus.SKIPPED:
            claims[m.content_key].append(m)
    for key, ms in claims.items():
        if len(ms) > 1:
            for m in ms:
                m.duplicate_with = [o.filename for o in ms if o is not m]
                m.notes.append(f"Content '{key}' is also assigned to: {', '.join(m.duplicate_with)}.")

    used = {m.content_key for m in matches if m.content_key}
    unused = [k for k in content_keys if k not in used]
    return MatchReport(matches, unused, dup_names)


def _auto_match(vid, name, keys, by_exact, by_stem, by_loose, numbered) -> Match:
    exact = by_exact.get(name.strip().lower(), [])
    if len(exact) == 1:
        return Match(vid, name, exact[0], "Filename", MatchStatus.MATCHED)
    if vid in keys:
        return Match(vid, name, vid, "Drive file ID", MatchStatus.MATCHED)
    cands = exact or by_stem.get(stem(name), [])
    if len(cands) == 1:
        return Match(vid, name, cands[0], "Filename (extension ignored)", MatchStatus.MATCHED)
    if len(cands) > 1:
        return Match(vid, name, None, "Ambiguous filename", MatchStatus.NEEDS_CONFIRMATION,
                     candidates=sorted(cands),
                     notes=[f"{len(cands)} content blocks match this filename – choose one."])
    similar = by_loose.get(loose(name), []) if loose(name) else []
    if len(similar) == 1:
        return Match(vid, name, similar[0], "Similar filename", MatchStatus.NEEDS_CONFIRMATION,
                     candidates=similar, notes=[f"Suggested '{similar[0]}' – please confirm."])
    if len(similar) > 1:
        return Match(vid, name, None, "Similar filename", MatchStatus.NEEDS_CONFIRMATION,
                     candidates=sorted(similar), notes=["Several similar content blocks – choose one."])
    n = _trailing_number(name)
    if n is not None and n in numbered:
        key = numbered[n]
        return Match(vid, name, key, "Video number", MatchStatus.NEEDS_CONFIRMATION,
                     candidates=[key], notes=[f"Suggested '{key}' by number – please confirm."])
    return Match(vid, name, None, "", MatchStatus.UNMATCHED,
                 notes=["No Gemini content found for this filename."])


def build_records(
    videos: list[dict],
    content: dict[str, dict[Platform, PlatformContent]],
    overrides: dict[str, str] | None = None,
    confirmed: set[str] | None = None,
    skipped: set[str] | None = None,
    existing: list[VideoRecord] | None = None,
) -> list[VideoRecord]:
    """Create/refresh VideoRecords from the match report (edits kept while mapping is unchanged)."""
    from src.validation import revalidate

    report = match_videos(videos, list(content), overrides, confirmed, skipped)
    known = {r.video_id: r for r in existing or []}
    records = []
    for idx, (v, m) in enumerate(zip(videos, report.matches)):
        rec = known.get(v["id"])
        if rec is None:
            rec = VideoRecord(video_id=v["id"], filename=v["name"],
                              thumbnail_url=v.get("thumbnail", ""),
                              simulate_failure=(idx == 3))  # mock publisher hook only
            rec.content_key = "\0"  # force content load below
        if rec.content_key != m.content_key:
            rec.content = copy.deepcopy(content.get(m.content_key, {})) if m.content_key else {}
        rec.filename = v["name"]
        rec.content_key = m.content_key
        rec.match_status, rec.match_method = m.status, m.method
        rec.match_notes, rec.duplicate_with = list(m.notes), list(m.duplicate_with)
        rec.skipped = m.status == MatchStatus.SKIPPED
        revalidate(rec)
        records.append(rec)
    return records

