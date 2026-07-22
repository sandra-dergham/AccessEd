import re

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".wmv", ".mkv", ".webm", ".mpeg", ".mpg"}


TRANSCRIPT_POSITIVE_RE = re.compile(
    r"\b(?:"
    r"transcript(?:\s+(?:is\s+)?(?:available|provided|included))?"
    r"|full transcript"
    r"|see transcript"
    r"|transcript below"
    r"|transcript attached"
    r"|text transcript"
    r"|written transcript"
    r")\b",
    re.I,
)

TRANSCRIPT_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"no transcript"
    r"|without transcript"
    r"|transcript(?:\s+is)?\s+not\s+available"
    r"|transcript(?:\s+is)?\s+unavailable"
    r"|transcript(?:\s+is)?\s+not\s+provided"
    r"|transcript missing"
    r")\b",
    re.I,
)

CAPTION_POSITIVE_RE = re.compile(
    r"\b(?:"
    r"captioned"
    r"|captions?(?:\s+(?:are\s+|is\s+)?(?:available|provided|included))?"
    r"|closed captions?"
    r"|open captions?"
    r"|subtitles?(?:\s+(?:are\s+|is\s+)?(?:available|provided|included))?"
    r"|cc\b"
    r")\b",
    re.I,
)

CAPTION_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"no captions?"
    r"|without captions?"
    r"|captions?(?:\s+are|\s+is)?\s+not\s+available"
    r"|captions?(?:\s+are|\s+is)?\s+unavailable"
    r"|not captioned"
    r"|no subtitles?"
    r"|without subtitles?"
    r"|subtitles?(?:\s+are|\s+is)?\s+not\s+available"
    r"|subtitles?(?:\s+are|\s+is)?\s+unavailable"
    r")\b",
    re.I,
)

AUDIO_DESC_POSITIVE_RE = re.compile(
    r"\b(?:"
    r"audio description(?:\s+(?:is\s+)?(?:available|provided|included))?"
    r"|described video"
    r"|descriptive audio"
    r"|audio-described"
    r"|audio described"
    r")\b",
    re.I,
)

AUDIO_DESC_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"no audio description"
    r"|without audio description"
    r"|audio description(?:\s+is)?\s+not\s+available"
    r"|audio description(?:\s+is)?\s+unavailable"
    r"|not described"
    r")\b",
    re.I,
)

MEDIA_ALT_POSITIVE_RE = re.compile(
    r"\b(?:"
    r"media alternative"
    r"|text alternative"
    r"|alternative for time-based media"
    r"|alternative version"
    r")\b",
    re.I,
)

MEDIA_ALT_NEGATIVE_RE = re.compile(
    r"\b(?:"
    r"no media alternative"
    r"|without media alternative"
    r"|media alternative(?:\s+is)?\s+not\s+available"
    r"|media alternative(?:\s+is)?\s+unavailable"
    r"|no text alternative"
    r"|without text alternative"
    r"|text alternative(?:\s+is)?\s+not\s+available"
    r"|text alternative(?:\s+is)?\s+unavailable"
    r")\b",
    re.I,
)

LIVE_RE = re.compile(
    r"\b(?:live|livestream|live stream|webcast|broadcast)\b",
    re.I,
)


LIVE_KEYWORDS = [
    "live",
    "livestream",
    "webcast",
    "broadcast",
]

ALT_TEXT_KEYWORDS = [
    "transcript",
    "media alternative",
    "text alternative",
    "alternative for time-based media",
    "audio description",
    "described video",
]




EXPLICIT_COLOR_ONLY_PATTERNS = [
    re.compile(
        r"\b(required|mandatory)\s+fields?\s+(are|is)\s+(marked|shown|indicated|highlighted|displayed)\s+(in|with|by|using)\s+(red|green|blue|yellow|orange|purple|pink|grey|gray)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(red|green|blue|yellow|orange|purple|pink|grey|gray)\s+(means?|indicates?|shows?|represents?|denotes?|signals?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcolor[- ]coded\b", re.IGNORECASE),
    re.compile(
        r"\b(items?|fields?|rows?|cells?|entries|text)\s+(in|shown in|marked in|highlighted in)\s+(red|green|blue|yellow|orange|purple|pink|grey|gray)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(click|select|choose)\s+the\s+(red|green|blue|yellow|orange|purple|pink|grey|gray)\s+(button|link|tab|option|item)\b",
        re.IGNORECASE,
    ),
]

_REQUIRED_CUE_PATTERN = re.compile(r"(^\*$)|(\*)|\brequired\b|\bmandatory\b", re.IGNORECASE)

NEUTRAL_REPEATABLE_LABELS = {
    "status", "label", "item", "type", "level", "flag", "category", "state"
}

MARKER_TEXTS = {
    "●", "•", "○", "◦", "▪", "■", "□", "▲", "△", "▼", "▽", "◆", "◇", "★", "☆"
}

EXCLUDED_SEMANTIC_LABELS = {
    "active", "inactive", "enabled", "disabled",
    "open", "closed", "approved", "rejected",
    "pass", "fail", "passed", "failed",
    "yes", "no", "true", "false",
    "success", "error", "warning",
    "valid", "invalid"
}


BAD_ALT_WORDS = {
    "image", "picture", "photo", "graphic", "figure",
    "icon", "logo", "video", "audio", "media"
}

