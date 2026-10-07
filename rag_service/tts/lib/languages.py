def _parse_supported_languages(raw: str) -> dict:
    mapping = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        code, _, voice = line.partition("=")
        code = code.strip().lower()
        voice = voice.strip()
        if code and voice:
            mapping[code] = voice
    return mapping


class LanguageTable:
    def __init__(self, raw_supported_languages: str):
        self._voice_by_code = _parse_supported_languages(raw_supported_languages)
        self._aliases = {code: code for code in self._voice_by_code}
        self._aliases.update({
            "english": "english", "en": "english",
            "hindi": "hindi", "hi": "hindi",
            "marathi": "marathi", "mr": "marathi",
            "kannada": "kannada", "kn": "kannada", "ka": "kannada",
            "punjabi": "punjabi", "pa": "punjabi",
        })

    def normalize(self, value):
        if not isinstance(value, str) or not value.strip():
            return None
        code = self._aliases.get(value.strip().lower())
        if code and code in self._voice_by_code:
            return code
        return None

    def voice_for(self, language_code):
        return self._voice_by_code.get(language_code)


_cached_table = None
_cached_raw = None


def _table():
    global _cached_table, _cached_raw
    from .settings import load as load_settings
    raw = load_settings().supported_languages
    if raw != _cached_raw:
        _cached_table = LanguageTable(raw)
        _cached_raw = raw
    return _cached_table


def normalize(value):
    return _table().normalize(value)


def voice_for(language_code):
    return _table().voice_for(language_code)


def reset_cache():
    global _cached_table, _cached_raw
    _cached_table = None
    _cached_raw = None
