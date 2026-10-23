"""Sample data: 10 videos with Gemini-style content (Hindi, emojis, hashtags, line breaks)."""

from __future__ import annotations

from src.content_parser import parse_gemini_text
from src.matching import build_records  # noqa: F401  (re-exported for callers/tests)
from src.models import VideoRecord

_PRODUCTS = [
    ("Floral Kurti", "फ्लोरल कुर्ती"),
    ("Silk Saree", "सिल्क साड़ी"),
    ("Denim Jacket", "डेनिम जैकेट"),
    ("Oxidised Jhumkas", "ऑक्सीडाइज़्ड झुमके"),
    ("Linen Co-ord Set", "लिनन को-ऑर्ड सेट"),
    ("Block Print Dupatta", "ब्लॉक प्रिंट दुपट्टा"),
    ("Kolhapuri Sandals", "कोल्हापुरी चप्पल"),
    ("Anarkali Gown", "अनारकली गाउन"),
    ("Tote Bag", "टोट बैग"),
    ("Chikankari Top", "चिकनकारी टॉप"),
]


def build_sample_gemini_text() -> str:
    blocks = []
    for i, (en, hi) in enumerate(_PRODUCTS, start=1):
        tag = en.replace(" ", "").replace("-", "")
        fb_desc = "" if i == 7 else (
            f"Aaj ka fashion find: {en} 😍\n{hi} – budget mein best!\n\n"
            "Link comment mein 👇"
        )
        blocks.append(
            f"""=== VIDEO: reel_{i:02d}.mp4 ===
--- YouTube Shorts ---
Hook: Ye {en} dekh ke sab poochenge – kahan se liya? 🤔
Title: {en} under ₹999 😱 #Shorts
Description: {hi} – trendy aur affordable! ✨
Daily fashion finds ke liye subscribe karein.
Hashtags: #DailyFinds #{tag} #Shorts
YouTube Tags: {en.lower()}, budget fashion, daily finds
On-screen Text: {hi} 👉 sirf ₹999
Transcript: Doston, aaj ka find hai {en}!
{hi} – quality zabardast, price kam.
--- Instagram Reels ---
Reel Cover Title: {en} Find 🔥
Caption: {en} jo sabko pasand aayega 💖
{hi} – perfect for every occasion ✨

Save karo, share karo! 📌
Hashtags: #DailyFinds #{tag} #FashionReels #OOTD #देसीस्टाइल
On-screen Text: {hi} 👉 sirf ₹999
Optimized Transcript: {en} under ₹999 – {hi}! Link bio mein 🔗
--- Facebook Reels ---
SEO Title: {en} – Best Budget Find | Daily Finds
Description: {fb_desc}
Hashtags: #DailyFinds #{tag} #IndianFashion
"""
        )
    return "\n".join(blocks)


SAMPLE_GEMINI_TEXT = build_sample_gemini_text()
SAMPLE_DRIVE_URL = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
SAMPLE_GEMINI_URL = "https://gemini.google.com/share/abc123def456"


def mock_list_videos(folder_url: str) -> list[dict]:
    """Stand-in for DriveClient.list_videos. Includes one unmatched video."""
    files = [{"id": f"vid{i:02d}", "name": f"reel_{i:02d}.mp4"} for i in range(1, 11)]
    files[9]["name"] = "reel_extra_clip.mp4"  # will not match any content
    return files


def load_sample_records() -> list[VideoRecord]:
    return build_records(mock_list_videos(SAMPLE_DRIVE_URL), parse_gemini_text(SAMPLE_GEMINI_TEXT))

