"""Canonical language labels for catalog presentation and language comparison.

Original source declarations remain in source files and inspection evidence.
Regional/script subtags do not create separate Library language groups.
"""
import re


_ALIASES = {
    'eng': 'en', 'pol': 'pl', 'fra': 'fr', 'fre': 'fr', 'deu': 'de', 'ger': 'de',
    'spa': 'es', 'ita': 'it', 'por': 'pt', 'nld': 'nl', 'dut': 'nl',
    'ces': 'cs', 'cze': 'cs', 'slk': 'sk', 'slo': 'sk', 'ukr': 'uk', 'rus': 'ru',
    'bel': 'be', 'bul': 'bg', 'hrv': 'hr', 'srp': 'sr', 'slv': 'sl',
    'swe': 'sv', 'dan': 'da', 'nor': 'no', 'nob': 'nb', 'nno': 'nn',
    'fin': 'fi', 'hun': 'hu', 'ron': 'ro', 'rum': 'ro', 'ell': 'el', 'gre': 'el',
    'tur': 'tr', 'ara': 'ar', 'heb': 'he', 'jpn': 'ja', 'kor': 'ko',
    'zho': 'zh', 'chi': 'zh', 'hin': 'hi', 'lit': 'lt', 'lav': 'lv', 'est': 'et',
}


def language_code(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower().replace('_', '-')
    if not re.fullmatch(r'[a-z]{2,3}(?:-[a-z0-9]{2,8})*', value):
        return None
    primary = value.split('-', 1)[0]
    if primary in {'und', 'zxx'}:
        return None
    # Preserve unlisted three-letter language codes instead of guessing/truncating.
    return _ALIASES.get(primary, primary)
