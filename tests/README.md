# Tests

Run: `python -m pytest -q tests`

`test_dashboard_logic.py` uses sample data for 10 videos (Hindi, emojis, hashtags, line breaks) and covers:
- Gemini text/JSON parsing
- Drive/Gemini URL validation
- Video-to-content matching (unmatched video, missing Facebook caption)
- Editing captions to fix validation
- Mock publishing, failure, Retry Failed, and duplicate protection
- Platform selection

