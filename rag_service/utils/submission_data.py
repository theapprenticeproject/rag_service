from typing import Dict, Optional


SUPPORTED_SUBMISSION_TYPES = {"image", "video", "audio", "text", "emoji"}
MEDIA_SUBMISSION_TYPES = {"image", "video", "audio"}
TEXT_SUBMISSION_TYPES = {"text", "emoji"}


def normalize_submission_payload(message_data: Dict) -> Dict:
    submission_type = (message_data.get("submission_type") or "").strip().lower()
    submission_url = _normalize_optional_str(message_data.get("submission_url"))
    submission_text = _normalize_optional_text(message_data.get("submission_text"))

    if submission_type not in SUPPORTED_SUBMISSION_TYPES:
        raise ValueError(
            f"Unsupported submission_type '{submission_type}'. Supported values: {sorted(SUPPORTED_SUBMISSION_TYPES)}"
        )

    if submission_type in MEDIA_SUBMISSION_TYPES and not submission_url:
        raise ValueError(f"submission_url is required for submission_type '{submission_type}'")

    if submission_type in TEXT_SUBMISSION_TYPES and not submission_text:
        raise ValueError(f"submission_text is required for submission_type '{submission_type}'")

    return {
        "submission_type": submission_type,
        "submission_url": submission_url,
        "submission_text": submission_text,
    }


def build_submission_content(submission_data: Dict) -> str:
    submission_type = submission_data.get("submission_type")
    if submission_type in MEDIA_SUBMISSION_TYPES:
        return submission_data.get("submission_url") or ""
    return submission_data.get("submission_text") or ""


def format_submission_text_for_prompt(submission_data: Dict) -> str:
    submission_type = submission_data.get("submission_type")
    submission_text = submission_data.get("submission_text") or ""
    if not submission_text:
        return ""

    if submission_type == "emoji":
        return f"Student submitted an emoji response: {submission_text}"

    return f"Student submitted the following text response:\n{submission_text}"


def _normalize_optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None
