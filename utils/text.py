"""
Text normalization utilities.
"""

import re
import unicodedata


def collapse_whitespace(text: str) -> str:
    """Replace multiple whitespace characters with a single space."""
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(text: str) -> str:
    """Light normalization that preserves emojis and readability."""
    return collapse_whitespace(text)


def truncate(text: str, max_len: int = 4000) -> str:
    """Basic truncation (compatibility)."""
    return smart_truncate(text, max_len)


def smart_truncate(text: str, max_len: int = 4000) -> str:
    """Truncate text to max_len, but try to cut at the last space to avoid split words."""
    if len(text) <= max_len:
        return text
    
    # Try to find the last space before the limit
    s = text[:max_len]
    cut_pos = s.rfind(" ")
    if cut_pos == -1 or cut_pos < max_len * 0.5:
        # If no space found or it's too far back, just cut exactly
        cut_pos = max_len - 1
        
    return text[:cut_pos].strip() + "…"


def scrub_markdown(text: str) -> str:
    """Aggressively remove all markdown and formatting symbols."""
    if not text:
        return ""
    
    # Remove common markdown formatting characters: * _ ~ ` # > | =
    text = re.sub(r"[\*_~`#\>\|=]", "", text)
    
    # Remove link markdown but keep text: [label](url) -> label
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    
    # Remove remaining standalone brackets
    text = re.sub(r"[\[\]\(\)]", "", text)
    
    # Normalize whitespace: remove multiple newlines and spaces
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    
    return text.strip()


def strip_urls(text: str) -> str:
    """Remove all URLs from text."""
    return re.sub(r"https?://\S+", "", text)


def remove_emoji(text: str) -> str:
    """Remove emoji characters (used for hash computation, not display)."""
    return "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("So", "Sk", "Sc")
    )


def make_summary_fallback(text: str, max_len: int = 80) -> str:
    """
    Create a basic summary from the first line/sentence of text.
    Used as a fallback when Gemini is unavailable.
    """
    if not text:
        return "—"
    # Take first line
    first_line = text.split("\n")[0].strip()
    # Remove URLs
    first_line = strip_urls(first_line).strip()
    # Truncate
    if len(first_line) > max_len:
        return first_line[:max_len - 1] + "…"
    return first_line if first_line else text[:max_len]
