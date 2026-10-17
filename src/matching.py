"""Deterministic matching of Drive videos to parsed Gemini content.

Rules (applied in this order, first hit wins):
1. Manual mapping chosen by the user (always authoritative).
2. Exact filename (case-insensitive)                     -> Matched
3. Drive file ID used as the content key                 -> Matched
4. Same filename ignoring extension                      -> Matched (if exactly one candidate)
5. Similar filename (only letters/digits compared)       -> Needs confirmation
6. Filename trailing number N and Gemini response N      -> Matched
   ('R-YT-10.mp4' -> response 10, never 1). Several files ending in N -> Needs confirmation;
   once one is confirmed the others are released. Drive listing order is never used.
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
NUMBER_METHOD = "Trailing number"

_TRAILING_NUM_RE = re.compile(r"(\d+)\s*$")
_RESPONSE_KEY_RE = re.compile(r"Video (\d+)")


def stem(name: str) -> str:
    name = (name or "").strip().lower()
    return name.rsplit(".", 1)[0] if "." in name else name


def loose(name: str) -> str:
    """Letters/digits only (keeps Devanagari) – used for 'similar' suggestions."""
    return re.sub(r"[^a-z0-9\u0900-\u097f]", "", stem(name))


def trailing_number(name: str) -> int | None:
    """Numeric identifier directly before the extension: 'R-YT-10.mp4' -> 10, 'M-New-02.mov' -> 2.

    The whole digit run is used, so 10 is never confused with 1."""
    m = _TRAILING_NUM_RE.search(stem(name))
    return int(m.group(1)) if m else None


_trailing_number = trailing_number  # backwards-compatible alias


def response_number(content_key: str | None) -> int | None:
    """'Video 10' -> 10 (numbered Gemini response), else None."""
    m = _RESPONSE_KEY_RE.fullmatch(content_key or "")
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

    @property
    def video_number(self) -> int | None:
        return trailing_number(self.filename)

    @property
    def response_number(self) -> int | None:
        return response_number(self.content_key)


@dataclass
class MatchReport:
    matches: list[Match]
    unused_content: list[str]
    duplicate_filenames: list[str]
    # trailing number -> filenames, when several Drive videos end in the same number
    duplicate_numbers: dict[int, list[str]] = field(default_factory=dict)
    # videos whose trailing number has no Gemini response (number -> filenames)
    missing_responses: dict[int, list[str]] = field(default_factory=dict)
    # Gemini response numbers not assigned to any video
    unmatched_responses: list[int] = field(default_factory=list)
    # videos without a trailing number while numbered responses exist
    unnumbered_videos: list[str] = field(default_factory=list)

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
    numbered = {response_number(k): k for k in keys if response_number(k) is not None}
    name_counts = defaultdict(int)
    num_groups: dict[int, list[dict]] = defaultdict(list)
    for v in videos:
        name_counts[v["name"].strip().lower()] += 1
        n = trailing_number(v["name"])
        if n is not None:
            num_groups[n].append(v)
    dup_names = sorted({v["name"] for v in videos if name_counts[v["name"].strip().lower()] > 1})

    matches: list[Match] = []
    for v in videos:
        vid, name = v["id"], v["name"]
        m = _auto_match(vid, name, keys, by_exact, by_stem, by_loose, numbered, num_groups)
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

    # Several files share number N: once the user confirms one, release the others.
    by_num_key = defaultdict(list)
    for m in matches:
        if m.content_key and m.method.startswith(NUMBER_METHOD) and m.status != MatchStatus.SKIPPED:
            by_num_key[m.content_key].append(m)
    for key, group in by_num_key.items():
        chosen = [m for m in group if m.status == MatchStatus.MATCHED]
        if len(group) > 1 and len(chosen) == 1:
            for m in group:
                if m is not chosen[0]:
                    m.content_key, m.status = None, MatchStatus.UNMATCHED
                    m.notes = [f"Response {response_number(key)} was assigned to "
                               f"{chosen[0].filename}. Choose other content or skip this video."]

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
    report = MatchReport(matches, unused, dup_names)
    if numbered:
        report.duplicate_numbers = {n: sorted(v["name"] for v in vs)
                                    for n, vs in sorted(num_groups.items()) if len(vs) > 1}
        report.missing_responses = {n: sorted(v["name"] for v in vs)
                                    for n, vs in sorted(num_groups.items()) if n not in numbered}
        report.unmatched_responses = sorted(n for n, k in numbered.items() if k not in used)
        report.unnumbered_videos = sorted(v["name"] for v in videos
                                          if trailing_number(v["name"]) is None)
    return report


def _auto_match(vid, name, keys, by_exact, by_stem, by_loose, numbered, num_groups) -> Match:
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
    n = trailing_number(name)
    if n is not None and n in numbered:
        key = numbered[n]
        method = f"{NUMBER_METHOD} {n} → response {n}"
        same = sorted(v["name"] for v in num_groups.get(n, []) if v["id"] != vid)
        if same:
            return Match(vid, name, key, method, MatchStatus.NEEDS_CONFIRMATION, candidates=[key],
                         notes=[f"Number {n} is also used by: {', '.join(same)}. Confirm the "
                                f"correct file for response {n}, or skip/remap the others."])
        return Match(vid, name, key, method, MatchStatus.MATCHED)
    if numbered and n is not None:
        return Match(vid, name, None, "", MatchStatus.UNMATCHED,
                     notes=[f"No Gemini response number {n} was found."])
    if numbered:
        return Match(vid, name, None, "", MatchStatus.UNMATCHED,
                     notes=["Filename has no trailing number – map it manually."])
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

