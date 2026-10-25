"""Reusable parser for Gemini responses (shared URL, pasted text or imported file).

Supports the numbered-response format with YouTube Shorts, Instagram Reels and Facebook Reels
sections, in plain text or Gemini-style Markdown, e.g.:

    === VIDEO: reel_01.mp4 ===          (also "## Video 1: x.mp4", "## Response 3")
    ### 1. YouTube Shorts 🎬                  (parsed for reference only – never published)
    **Hook:** ...
    ### INSTAGRAM REELS
    **Instagram Caption:**
    multi-line text ✨ हिंदी
    **Instagram Hashtags:** #a #b
    **SEO Keywords:** ...                     (ignored – ends the previous field)
    ### FACEBOOK REELS
    **Facebook Description:** ...
    **Facebook Hashtags:** ...

Only fields inside the matching platform section are used, so Instagram content never
becomes Facebook content (or vice versa). Labels such as SEO keywords, search phrases,
share/save/comment triggers or optimized transcripts end the current field and are never
appended to a caption. Values are copied verbatim (Hindi, emojis, line breaks, hashtags).
Missing or ambiguous fields are reported as ``ParseIssue`` and never guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from src.models import Platform, PlatformContent

# ------------------------------------------------------------------ field spec
# Fields required for publishing (missing ones are flagged for review).
# YouTube Shorts content is parsed for reference only – it is never published or flagged.
EXPECTED_FIELDS: dict[Platform, list[str]] = {
    Platform.INSTAGRAM: ["caption", "hashtags"],
    Platform.FACEBOOK: ["description", "hashtags"],
}

FIELD_LABELS = {
    "title": "Title", "seo_title": "SEO title", "hook": "Hook", "caption": "Caption",
    "description": "Description", "hashtags": "Hashtags", "youtube_tags": "YouTube tags",
    "cover_title": "Reel cover title", "on_screen_text": "On-screen text",
    "transcript": "Transcript",
}

_ALIASES = {
    "title": "title", "video title": "title", "short title": "title", "youtube title": "title",
    "seo title": "seo_title", "seo optimized title": "seo_title", "facebook title": "seo_title",
    "hook": "hook", "opening hook": "hook", "video hook": "hook",
    "caption": "caption", "instagram caption": "caption", "reel caption": "caption",
    "facebook caption": "caption", "post caption": "caption",
    "description": "description", "seo description": "description",
    "video description": "description", "facebook description": "description",
    "seo optimized description": "description", "youtube description": "description",
    "post description": "description",
    "hashtags": "hashtags", "hashtag": "hashtags", "trending hashtags": "hashtags",
    "youtube tags": "youtube_tags", "tags": "youtube_tags", "seo tags": "youtube_tags",
    "reel cover title": "cover_title", "cover title": "cover_title", "cover text": "cover_title",
    "thumbnail text": "cover_title",
    "on screen text": "on_screen_text", "text overlay": "on_screen_text",
    "on screen text overlay": "on_screen_text", "screen text": "on_screen_text",
    "transcript": "transcript", "optimized transcript": "transcript",
    "seo optimized transcript": "transcript", "voiceover": "transcript",
    "voiceover script": "transcript", "script": "transcript",
}

# Platform-specific meaning of a generic label (e.g. Facebook "Title" = SEO title).
_PLATFORM_OVERRIDES = {
    Platform.FACEBOOK: {"title": "seo_title"},
}

IGNORED_FIELD = "__ignored__"  # label for another platform -> skip its text
_LABEL_PLATFORM = {
    "instagram": Platform.INSTAGRAM, "insta": Platform.INSTAGRAM, "ig": Platform.INSTAGRAM,
    "facebook": Platform.FACEBOOK, "fb": Platform.FACEBOOK,
    "youtube": Platform.YOUTUBE, "yt": Platform.YOUTUBE, "shorts": Platform.YOUTUBE,
}

_VIDEO_EXT = r"\.(?:mp4|mov|m4v|avi|mkv|webm)"
_VIDEO_RE = re.compile(
    r"^[\s#*=_>\-]*(?:video|response|reel|clip|request)\s*(?:(?:no\.?|number)\s*)?"
    r"(?:#?\s*(?P<num>\d+))?\s*[:\-–—.)]?\s*"
    r"(?P<name>[^*=#]*?" + _VIDEO_EXT + r")?[\s*=#_]*$",
    re.I,
)
_FIELD_RE = re.compile(
    r"^\s*(?:[-*•]\s+)?(?:\d+[.)]\s*)?[*_]*\s*(?P<label>[A-Za-z][A-Za-z0-9 \-/()'&]{0,48}?)"
    r"\s*[*_]*\s*[:：]\s*[*_]*\s*(?P<value>.*)$"
)
_PLATFORM_NORM = re.compile(
    r"^(?:\d+ )?(?:for )?(?:(youtube) shorts?|(instagram) reels?|(facebook) reels?|(youtube)|"
    r"(instagram)|(facebook))(?: (?:content|section|post|version|copy|details))?$"
)
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
# A label alone on its line ("Hashtags", "**Caption**", "### Description"), value below it.
_BARE_LABEL_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:[-*•]\s+)?(?:\d+[.)]\s*)?[*_]*\s*"
    r"(?P<label>[A-Za-z][A-Za-z0-9 \-/()'&]{0,48}?)\s*[*_]*\s*[:：]?\s*$")


_NUMBERED_RE = re.compile(r"^\s*(?:#{1,6}\s+)?[*_]*\d{1,2}[.)]\s+\S")
# '7. Instagram Search Phrases', '13. Give the affilate Product name…' (English section titles)
_NUMBERED_HEADING_RE = re.compile(r"^\s*[*_]*\d{1,2}[.)]\s+[*_]*[A-Za-z][A-Za-z0-9 ,'&/()\-]{2,100}[*_:]*\s*$")


def _is_caps_heading(label: str) -> bool:
    letters = [c for c in label if c.isalpha()]
    return (len(letters) >= 3 and all(c.isupper() for c in letters)
            and 2 <= len(label.split()) <= 5)
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+\S")  # markdown heading (a '#hashtag' has no space)
# Conversation-turn markers in exported/printed chats: they end the current section so a
# user prompt is never appended to the previous response's hashtags.
_TURN_RE = re.compile(
    r"^\s*(?:you said|gemini said|gemini|show thinking|prompt|user|response|model|"
    r"edit|copy|share|regenerate)\s*[:：]?\s*$", re.I,
)
# Unrecognised labels with this wording are other fields (SEO keywords, triggers, ...):
# they end the current field so their text is never appended to a caption.
_OTHER_LABEL_RE = re.compile(
    r"\b(?:seo|keywords?|search|phrases?|triggers?|cta|call to action|transcript|script|tags?|"
    r"titles?|hook|tips?|notes?|engagement|thumbnail|cover|music|audio|sound|posting|best time|"
    r"target|audience|overlay|on screen|text|save|share|comment|pinned|alt|location|link|"
    r"duration|format|caption|description|hashtags?)\b",
    re.I,
)


@dataclass
class ParseIssue:
    video: str
    platform: str
    field: str
    severity: str  # "warning" (missing) | "error" (ambiguous/unassigned)
    message: str


@dataclass
class ParseResult:
    content: dict[str, dict[Platform, PlatformContent]] = field(default_factory=dict)
    issues: list[ParseIssue] = field(default_factory=list)

    def issues_for(self, video: str) -> list[ParseIssue]:
        return [i for i in self.issues if i.video == video]

    @property
    def has_content(self) -> bool:
        return any(self.content.values())


# ------------------------------------------------------------------ helpers
def _norm(text: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return re.sub(r"\s+", " ", t).strip()


def detect_platform(line: str) -> Platform | None:
    """Return the platform if the line is a section header (and nothing else)."""
    if len(line) > 80:
        return None
    if _FIELD_RE.match(line) and not line.rstrip(" *_").endswith(":"):
        return None  # "Instagram: follow us" is text, not a header
    m = _PLATFORM_NORM.match(_norm(re.sub(r"\(.*?\)", " ", line)))
    if not m:
        return None
    g = [x for x in m.groups() if x][0]
    return {"youtube": Platform.YOUTUBE, "instagram": Platform.INSTAGRAM}.get(g, Platform.FACEBOOK)


def detect_video(line: str) -> str | None:
    """Return a video key ('file.mp4' or 'Video 3') if the line is a video header.

    Numbered headers ('Video 3', 'Response 3', 'Reel #3', 'Request No. 3') all become
    'Video 3' (leading zeros dropped) so they can be matched by trailing number."""
    m = _VIDEO_RE.match(line)
    if not m or not (m.group("num") or m.group("name")):
        return None
    return (m.group("name") or "").strip() or f"Video {int(m.group('num'))}"


def resolve_field(label: str, platform: Platform | None) -> str | None:
    """Map a field label to a PlatformContent attribute.

    Returns ``IGNORED_FIELD`` for labels naming another platform (e.g. 'Instagram Caption'
    inside the Facebook section) so content never crosses platforms."""
    key = _norm(re.sub(r"\(.*?\)", "", label))
    label_platform = _LABEL_PLATFORM.get(key.split(" ", 1)[0]) if key else None
    if label_platform and platform and label_platform != platform:
        return IGNORED_FIELD
    for prefix in ("instagram ", "insta ", "ig ", "facebook ", "fb ", "youtube ", "yt ",
                   "reel ", "reels "):
        if key not in _ALIASES and key.startswith(prefix):
            key = key[len(prefix):]
    name = _ALIASES.get(key)
    if name and platform in _PLATFORM_OVERRIDES:
        name = _PLATFORM_OVERRIDES[platform].get(name, name)
    return name


def _is_other_label(m: re.Match, line: str) -> bool:
    """An unrecognised 'Label:' line that is clearly a field header (bold or known wording)."""
    label = m.group("label").strip()
    if len(label.split()) > 5:
        return False
    return "**" in line[: m.end("label") + 4] or bool(_OTHER_LABEL_RE.search(label))


def _clean(lines: list[str]) -> str:
    text = "\n".join(lines).strip()
    if len(text) > 4 and text.startswith("**") and text.endswith("**") and "**" not in text[2:-2]:
        text = text[2:-2].strip()
    return text


# ------------------------------------------------------------------ section label scanner
# Every label Gemini uses inside a platform section, mapped to the field it fills
# (None = a field we don't use; it only ends the previous one). Longest labels first.
_SCAN_LABELS: list[tuple[str, str | None]] = sorted([
    ("instagram caption", "caption"), ("reel caption", "caption"), ("caption", "caption"),
    ("facebook caption", "caption"),
    ("facebook reel description", "description"), ("facebook reels description", "description"),
    ("facebook description", "description"), ("reel description", "description"),
    ("post description", "description"), ("description", "description"),
    ("instagram hashtags", "hashtags"), ("facebook hashtags", "hashtags"),
    ("hashtags", "hashtags"), ("hashtag", "hashtags"),
    ("hook", None), ("reel cover title", None), ("reel cover text", None), ("cover title", None),
    ("seo title", None), ("title", None), ("primary seo keyword", None),
    ("secondary seo keywords", None), ("seo keywords", None), ("keywords", None),
    ("instagram search phrases", None), ("facebook search phrases", None),
    ("search phrases", None), ("search intent questions", None), ("share trigger", None),
    ("save trigger", None), ("comment trigger", None), ("on screen text", None),
    ("optimized transcript", None), ("transcript", None), ("content category", None),
    ("youtube tags", None), ("shorts description", None), ("optimized video topic", None),
    ("give the affilate product name", None), ("give the affiliate product name", None),
    ("affiliate product", None),
], key=lambda x: -len(x[0]))
_SCAN_RES = [
    (re.compile(r"^\s*(?:#{1,6}\s+)?(?:[-*•]\s*)?(?:\d{1,2}\s*[.)]\s*)?[*_\s]*"
                + r"[\s\-]*".join(map(re.escape, label.split()))
                + r"\b[*_\s]*(?:\([^)]*\))?[*_\s]*[:：\-–—]?\s*(?P<value>.*)$", re.I), fname)
    for label, fname in _SCAN_LABELS
]
_NUMBER_ONLY_RE = re.compile(r"^\s*\d{1,2}\s*[.)]?\s*$")


def _scan_section(lines: list[str]) -> dict[str, str]:
    """Label-based extraction for one platform section: a field runs from its label (with or
    without number/colon, value on the same or following lines) to the next known label."""
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    cur = ""
    for line in lines:
        if _NUMBER_ONLY_RE.match(line):
            continue  # list number printed on its own line
        hit = next(((rx, f) for rx, f in _SCAN_RES if rx.match(line)), None)
        if hit:
            rx, fname = hit
            if fname and fname not in found:
                current, cur = [rx.match(line).group("value")], fname
                found[fname] = current
            else:
                current = None
            continue
        if current is None:
            continue
        if _RULE_RE.match(line) or _NUMBERED_HEADING_RE.match(line) or _HEADING_RE.match(line):
            current = None
            continue
        if (cur == "hashtags" and line.strip() and not line.lstrip().startswith("#")
                and any(x.strip() for x in current)):
            current = None
            continue
        current.append(line)
    return {k: v for k, v in ((k, _clean(v)) for k, v in found.items()) if v}


# ------------------------------------------------------------------ parser
def parse_gemini_response(text: str) -> ParseResult:
    result = ParseResult()
    raw: dict[str, dict[Platform, dict[str, list[list[str]]]]] = {}
    video_order: list[str] = []
    video: str | None = None
    platform: Platform | None = None
    current: list[str] | None = None
    cur_name = ""
    sections: dict[str, dict[Platform, list[str]]] = {}
    orphan_lines = 0
    implicit_used = False
    implicit_keys: list[str] = []

    def start_video(key: str):
        nonlocal video, platform, current
        if key in raw:
            result.issues.append(ParseIssue(
                key, "", "", "error",
                f"Video '{key}' appears more than once; sections merged – please review."))
        else:
            raw[key] = {}
            video_order.append(key)
        video, platform, current = key, None, None

    for line in (text or "").replace("\r\n", "\n").splitlines():
        vkey = detect_video(line)
        if vkey:
            start_video(vkey)
            continue
        if _TURN_RE.match(line):
            platform, current = None, None
            continue
        p = detect_platform(line)
        if p:
            # Header-less responses (pasted one after another): a new response begins when a
            # platform section starts outside any video, repeats in the current implicit block,
            # or YouTube Shorts (the first section) appears after Instagram/Facebook.
            if video is None or (video in implicit_keys and (
                    p in raw[video] or (p == Platform.YOUTUBE and raw[video]))):
                implicit_used = True
                key = f"Video {len(implicit_keys) + 1}"
                while key in raw:
                    key = f"Video {int(key.split()[1]) + 1}"
                implicit_keys.append(key)
                start_video(key)
            platform, current = p, None
            raw[video].setdefault(p, {})
            sections.setdefault(video, {}).setdefault(p, [])
            continue
        if platform and video:
            sections[video][platform].append(line)
        m = _FIELD_RE.match(line) if platform else None
        fname = resolve_field(m.group("label"), platform) if m else None
        if platform and not m:
            bare = _BARE_LABEL_RE.match(line)
            if bare:
                bname = resolve_field(bare.group("label"), platform)
                if bname == IGNORED_FIELD:
                    current = None
                    continue
                if bname:
                    current, cur_name = [], bname
                    raw[video][platform].setdefault(bname, []).append(current)
                    continue
                if _is_caps_heading(bare.group("label")) or _NUMBERED_HEADING_RE.match(line):
                    # 'AFFILIATE PRODUCT', '7. Instagram Search Phrases', '8. Share Trigger'
                    current = None  # e.g. 'AFFILIATE PRODUCT' – a new, unrelated block
                    continue
        if fname == IGNORED_FIELD or (m and fname is None and _is_other_label(m, line)):
            current = None  # e.g. 'SEO Keywords:', 'Share Trigger:' – never part of a caption
            continue
        if fname:
            current, cur_name = [m.group("value")], fname
            raw[video][platform].setdefault(fname, []).append(current)
            continue
        if current is not None:
            if (_RULE_RE.match(line) or _HEADING_RE.match(line)
                    or (not m and _NUMBERED_HEADING_RE.match(line))):  # rule / heading ends a field
                current = None
                continue
            if (cur_name == "hashtags" and line.strip() and not line.lstrip().startswith("#")
                    and any(x.strip() for x in current)):
                current = None  # hashtag list ended; following text is not a hashtag
                continue
            current.append(line)
        elif line.strip() and video is not None and platform is None:
            orphan_lines += 1

    for vkey in video_order:
        result.content[vkey] = {}
        for plat, fields in raw[vkey].items():
            pc = PlatformContent()
            for fname, occurrences in fields.items():
                non_empty = [v for v in (_clean(o) for o in occurrences) if v]
                if len(set(non_empty)) > 1 and fname in EXPECTED_FIELDS.get(plat, []):
                    result.issues.append(ParseIssue(
                        vkey, plat.value, fname, "error",
                        f"{plat.value}: {FIELD_LABELS[fname]} is ambiguous ({len(non_empty)} "
                        "different values); left empty – please choose and edit manually."))
                    continue
                setattr(pc, fname, non_empty[0] if non_empty else "")
            ambiguous = {i.field for i in result.issues
                         if i.video == vkey and i.platform == plat.value}
            wanted = EXPECTED_FIELDS.get(plat, [])
            if any(not getattr(pc, f) and f not in ambiguous for f in wanted):
                # Fallback: label scan of the section's raw lines (PDF layouts the line
                # parser misses). Only fills fields that are still empty.
                scanned = _scan_section(sections.get(vkey, {}).get(plat, []))
                for f in wanted:
                    if not getattr(pc, f) and f not in ambiguous and scanned.get(f):
                        setattr(pc, f, scanned[f])
                if (plat == Platform.FACEBOOK and not pc.description and not pc.caption
                        and scanned.get("caption")):
                    pc.caption = scanned["caption"]
            result.content[vkey][plat] = pc
        _flag_missing(result, vkey)

    if implicit_used:
        result.issues.append(ParseIssue(
            "", "", "", "warning",
            f"No 'Video N' headers found: {len(implicit_keys)} response(s) were numbered 1–"
            f"{len(implicit_keys)} in the order they appear (each starts with its YouTube "
            "Shorts / Instagram section). Check the mapping before using the captions."))
    for vkey in video_order:
        if vkey.startswith("Video ") and vkey not in implicit_keys:
            result.issues.append(ParseIssue(
                vkey, "", "", "warning",
                f"'{vkey}' has no filename; it will be matched to the Drive video whose filename "
                f"ends in {vkey.split()[1]} – please review the mapping."))
    if orphan_lines and video_order:
        result.issues.append(ParseIssue(
            "", "", "", "warning",
            f"{orphan_lines} line(s) outside any platform section were ignored."))
    if not video_order:
        result.issues.append(ParseIssue(
            "", "", "", "error",
            "No YouTube Shorts, Instagram Reels or Facebook Reels sections were found."))
    return result


def _flag_missing(result: ParseResult, vkey: str) -> None:
    platforms = result.content[vkey]
    for plat, expected in EXPECTED_FIELDS.items():
        if plat not in platforms:
            result.issues.append(ParseIssue(vkey, plat.value, "", "warning",
                                            f"{plat.value} section is missing."))
            continue
        pc = platforms[plat]
        ambiguous = {i.field for i in result.issues
                     if i.video == vkey and i.platform == plat.value}
        for f in expected:
            if f in ambiguous:
                continue
            if f == "description" and plat == Platform.FACEBOOK and pc.caption:
                continue  # Facebook 'Caption' is accepted as the description
            if not getattr(pc, f):
                result.issues.append(ParseIssue(
                    vkey, plat.value, f, "warning",
                    f"{plat.value}: {FIELD_LABELS[f]} not found – please review."))


# ------------------------------------------------------------------ compat / other formats
def parse_gemini_text(text: str) -> dict[str, dict[Platform, PlatformContent]]:
    """Return {filename: {platform: PlatformContent}} (used by caption matching)."""
    return parse_gemini_response(text).content


def parse_json(text: str) -> dict[str, dict[Platform, PlatformContent]]:
    """Parse JSON: {"video_01.mp4": {"Instagram": {"caption": ...}, ...}}."""
    data = json.loads(text)
    out: dict[str, dict[Platform, PlatformContent]] = {}
    for filename, platforms in data.items():
        out[filename] = {}
        for pname, fields in platforms.items():
            platform = detect_platform(pname) or Platform.FACEBOOK
            allowed = {k: v for k, v in fields.items() if k in PlatformContent.__dataclass_fields__}
            out[filename][platform] = PlatformContent(**allowed)
    return out


def parse_uploaded_result(name: str, raw: bytes) -> ParseResult:
    text = raw.decode("utf-8-sig")
    if name.lower().endswith(".json"):
        return ParseResult(content=parse_json(text))
    return parse_gemini_response(text)


def parse_uploaded(name: str, raw: bytes) -> dict[str, dict[Platform, PlatformContent]]:
    return parse_uploaded_result(name, raw).content

