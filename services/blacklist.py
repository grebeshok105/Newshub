"""
Blacklist filter — blocks news containing unwanted keywords.
"""

from __future__ import annotations

import logging
import re

from config import BLACKLIST_WORDS

logger = logging.getLogger(__name__)


def is_blacklisted(text: str, extra_words: list[str] | None = None) -> bool:
    """
    Check if text contains any blacklisted word/phrase.

    Parameters
    ----------
    text : str
        The news text to check.
    extra_words : list[str], optional
        Additional blacklist words (on top of config).

    Returns
    -------
    bool
        True if text should be filtered out.
    """
    if not text:
        return False

    words = list(BLACKLIST_WORDS)
    if extra_words:
        words.extend([w.lower().strip() for w in extra_words])

    if not words:
        return False

    text_lower = text.lower()
    
    # ── URL Pattern Matching for Bookmakers/Casinos ──
    # Catch any common bet/casino domains even if they use subdomains
    bet_patterns = r"(1xbet|1win|pin-?up|mostbet|melbet|betboom|fonbet|cybershoke)\.(com|net|ru|me|site|pro)"
    if re.search(bet_patterns, text_lower):
        logger.debug("Blacklisted (URL pattern match)")
        return True

    for word in words:
        if not word:
            continue
        # Use word boundary search for short words to avoid false positives
        if len(word) <= 3:
            if re.search(rf"\b{re.escape(word)}\b", text_lower):
                logger.debug("Blacklisted (exact match): '%s'", word)
                return True
        else:
            if word in text_lower:
                logger.debug("Blacklisted (contains): '%s'", word)
                return True

    return False
