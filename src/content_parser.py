"""Reusable parser for Gemini responses (shared URL, pasted text or imported file).

Supports the existing StylePick format with YouTube Shorts, Instagram Reels and
Facebook Reels sections, in plain text or Gemini-style Markdown, e.g.:

    === VIDEO: stylepick_01.mp4 ===          (also "## Video 1: x.mp4", "**Video 2 – x.mov**")
    ### 1. YouTube Shorts 🎬
    **Hook:** ...
    **Title:** ...
    * **YouTube Tags:** ...
    ### Instagram Reels
    **Reel Cover Title:** ...
    **Caption:**
    multi-line text ✨ हिंदी
    ### Facebook Reels
    **SEO Title:** ...

Values are copied verbatim (Hindi, emojis, line breaks, hashtags preserved).
Missing or ambiguous fields are reported as ``ParseIssue`` and never guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from src.models import Platform, PlatformContent

# ------------------------------------------------------------------ field spec
EXPECTED_FIELDS: dict[Platform, list[str]] = {
    Platform.INSTAGRAM: ["cover_title", "caption", "hashtags", "on_screen_text", "transcript"],
    Platform.FACEBOOK: ["seo_title", "description", "hashtags"],
    Platform.YOUTUBE: ["hook", "title", "description", "hashtags", "youtube_tags",
                       "on_screen_text", "transcript"],
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

_VIDEO_EXT = r"\.(?:mp4|mov|m4v|avi|mkv|webm)"
_VIDEO_RE = re.compile(
    r"^[\s#*=_>\-]*video\s*(?:#?\s*(?P<num>\d+))?\s*[:\-–—.)]?\s*"
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
    m = _PLATFORM_NORM.match(_norm(line))
    if not m:
        return None
    g = [x for x in m.groups() if x][0]
    return {"youtube": Platform.YOUTUBE, "instagram": Platform.INSTAGRAM}.get(g, Platform.FACEBOOK)


def detect_video(line: str) -> str | None:
    """Return a video key ('file.mp4' or 'Video 3') if the line is a video header."""
    m = _VIDEO_RE.match(line)
    if not m or not (m.group("num") or m.group("name")):
        return None
    return (m.group("name") or "").strip() or f"Video {m.group('num')}"


def resolve_field(label: str, platform: Platform | None) -> str | None:
    key = _norm(re.sub(r"\(.*?\)", "", label))
    for prefix in ("instagram ", "facebook ", "youtube ", "reel ", "reels "):
        if key not in _ALIASES and key.startswith(prefix):
            key = key[len(prefix):]
    name = _ALIASES.get(key)
    if name and platform in _PLATFORM_OVERRIDES:
        name = _PLATFORM_OVERRIDES[platform].get(name, name)
    return name


def _clean(lines: list[str]) -> str:
    text = "\n".join(lines).strip()
    if len(text) > 4 and text.startswith("**") and text.endswith("**") and "**" not in text[2:-2]:
        text = text[2:-2].strip()
    return text


# ------------------------------------------------------------------ parser
def parse_gemini_response(text: str) -> ParseResult:
    result = ParseResult()
    raw: dict[str, dict[Platform, dict[str, list[list[str]]]]] = {}
    video_order: list[str] = []
    video: str | None = None
    platform: Platform | None = None
    current: list[str] | None = None
    orphan_lines = 0
    implicit_used = False

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
        p = detect_platform(line)
        if p:
            if video is None:  # platform sections without a video header
                implicit_used = True
                start_video("(unassigned)")
            platform, current = p, None
            raw[video].setdefault(p, {})
            continue
        m = _FIELD_RE.match(line) if platform else None
        fname = resolve_field(m.group("label"), platform) if m else None
        if fname:
            current = [m.group("value")]
            raw[video][platform].setdefault(fname, []).append(current)
            continue
        if current is not None:
            if _RULE_RE.match(line):  # markdown horizontal rule ends a field
                current = None
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
                if len(set(non_empty)) > 1:
                    result.issues.append(ParseIssue(
                        vkey, plat.value, fname, "error",
                        f"{plat.value}: {FIELD_LABELS[fname]} is ambiguous ({len(non_empty)} "
                        "different values); left empty – please choose and edit manually."))
                    continue
                setattr(pc, fname, non_empty[0] if non_empty else "")
            result.content[vkey][plat] = pc
        _flag_missing(result, vkey)

    if implicit_used:
        result.issues.append(ParseIssue(
            "(unassigned)", "", "", "error",
            "Content has platform sections but no video filename header "
            "(e.g. '=== VIDEO: name.mp4 ==='); it cannot be matched automatically."))
    for vkey in video_order:
        if vkey.startswith("Video "):
            result.issues.append(ParseIssue(
                vkey, "", "", "error",
                f"'{vkey}' has no filename; it cannot be matched to a Drive video automatically."))
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
                result.issues.append(ParseIssue(vkey, plat.value, f, "warning",
                                                f"{plat.value}: {FIELD_LABELS[f]} not found."))


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

