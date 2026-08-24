# rag_service/rag_service/core/llm_providers.py

import base64
import json
import mimetypes
import os
import tempfile
from typing import Any, List, Dict, Optional
from urllib.parse import urlparse

import requests
from together import Together
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from .llm_interface import BaseLLMInterface
from ..utils.media_types import detect_image_mime_type
import vertexai
from google.oauth2 import service_account
from vertexai.generative_models import GenerativeModel, Part

class OpenAIProvider(BaseLLMInterface):
    """OpenAI provider using LangChain"""
    
    def __init__(self, api_key: str, model_name: str, temperature: float = 0.7, max_tokens: int = 2000, settings: Any = None):
        super().__init__(api_key, model_name, temperature, max_tokens)
        self.llm = ChatOpenAI(
            model_name=model_name,
            openai_api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens
        )
    
    async def generate(self, messages: List[Dict]) -> str:
        langchain_messages = []
        for msg in messages:
            if msg["role"] == "system":
                langchain_messages.append(SystemMessage(content=msg["content"]))
            else:
                langchain_messages.append(HumanMessage(content=msg["content"]))
        
        response = await self.llm.agenerate([langchain_messages])
        return response.generations[0][0].text.strip()
    
    async def generate_with_vision(self, messages: List[Dict]) -> str:
        # OpenAI vision models handle image URLs in the content
        return await self.generate(messages)


class TogetherAIProvider(BaseLLMInterface):
    """Together AI provider optimized for Llama 3.2 90B Vision"""
    
    def __init__(self, api_key: str, model_name: str, temperature: float = 0.7, max_tokens: int = 15000, settings: Any = None):
        super().__init__(api_key, model_name, temperature, max_tokens)
        self.client = Together(api_key=api_key)

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            messages = self.format_messages(system_prompt, user_prompt)
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=self.temperature
            )
            return response.text
        except Exception as e:
            print(f"Error during Together AI generation: {e}")
            raise Exception(f"Error during Together AI generation: {e}")

    async def generate_with_vision(self, image_url: str, system_prompt: str, user_prompt: str = "") -> str:
        # Llama 3.2 90B Vision handles image URLs in the message content
        try:
            messages = self.format_messages(system_prompt, user_prompt, image_url)
            # print(f"\nFormatted messages for Together AI:\n{json.dumps(messages, indent=2)}")
            response = self.client.chat.completions.create(
                        # reasoning={"enabled": False},
                        reasoning_effort="low",
                        model=self.model_name,
                        messages=messages,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                )
            return response
        except Exception as e:
            print(f"Error during Together AI vision generation: {e}")
            raise Exception(f"Error during Together AI vision generation: {e}")
    
    def format_messages(self, system_prompt: str, user_prompt: str, image_url: Optional[str] = None) -> List[Dict]:
        """Format messages specifically for Llama 3.2 90B Vision"""
        messages = []
        
        # Llama 3.2 90B sometimes performs better with system prompts in user messages
        combined_prompt = f"{system_prompt}\n\n{user_prompt}"
        
        if image_url:
            # Llama 3.2 90B Vision expects this specific format
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": combined_prompt
                    },
                    {
                        "type": "image_url", 
                        "image_url": {
                            "url": image_url
                        }
                    }
                ]
            })
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
        return messages
    
    def calculate_cost(self, response):
        """
        Calculates the cost of a Together AI API call based on usage_metadata and input/output token costs.
        Supports Together AI models.
        
        :param response: The ChatCompletion response object.
        :param input_cost: Price per 1M input tokens.
        :param output_cost: Price per 1M output tokens.
        """
        # Extract token counts from the usage attribute
        model_name = response["model"]
        all_models = self.client.models.list()
        input_cost, output_cost = 0.0, 0.0
        for model in all_models:
            if model.id == model_name:
                input_cost = model.pricing.input
                output_cost = model.pricing.output
                break

        prompt_tokens = response["usage"]["prompt_tokens"]
        completion_tokens = response["usage"]["completion_tokens"]

        # Calculate costs (Standardizing to price per token by dividing by 1,000,000)
        total_input_cost = (prompt_tokens / 1_000_000) * input_cost
        total_output_cost = (completion_tokens / 1_000_000) * output_cost

        return total_input_cost + total_output_cost


class AnthropicResponse:
    """Small adapter matching the response surface used by Gemini call sites."""

    def __init__(self, data: Dict):
        self.data = data
        self.content = data.get("content", [])
        self.text = self._extract_text()

    def _extract_text(self) -> str:
        text_blocks = []
        for block in self.content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_blocks.append(block.get("text", ""))
        return "\n".join([text for text in text_blocks if text])

    def to_dict(self) -> Dict:
        return self.data


class AnthropicProvider(BaseLLMInterface):
    """Anthropic provider using the Claude Messages API."""

    API_URL = "https://api.anthropic.com/v1/messages"
    ANTHROPIC_VERSION = "2023-06-01"
    SUPPORTED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    DEFAULT_VIDEO_FRAME_COUNT = 6

    def __init__(
        self,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        settings: Any = None,
    ):
        super().__init__(api_key, model_name or "claude-sonnet-5", temperature, max_tokens)
        self.settings = settings
        self.api_key = self._resolve_api_key(api_key, settings)
        if not self.api_key:
            raise ValueError("Anthropic API key is required")

    def _resolve_api_key(self, api_key: str, settings: Any) -> str:
        if api_key and api_key.strip():
            return api_key.strip()

        if settings:
            password_getter = getattr(settings, "get_password", None)
            if callable(password_getter):
                for fieldname in ("api_secret", "api_key"):
                    try:
                        value = password_getter(fieldname)
                    except Exception:
                        value = None
                    if value:
                        return str(value).strip()

            for fieldname in ("api_secret", "api_key"):
                value = self._settings_value(settings, fieldname)
                if value:
                    return str(value).strip()

        return ""

    def _settings_value(self, settings: Any, fieldname: str) -> Any:
        getter = getattr(settings, "get", None)
        if callable(getter):
            value = getter(fieldname)
            if value:
                return value
        return getattr(settings, fieldname, None)

    async def generate(self, messages: List[Dict]) -> str:
        system_prompt, normalized_messages = self._normalize_messages(messages)
        payload = {
            "model": self.model_name,
            "max_tokens": self.max_tokens,
            "messages": normalized_messages,
        }
        if system_prompt:
            payload["system"] = system_prompt

        response = self._post_message(payload)
        cost = self.calculate_cost(response.to_dict())
        return response.text, cost or "", 0.0

    async def generate_with_vision(self, image_source, prompt: str, mime_type: Optional[str] = None) -> str:
        try:
            response = self._create_user_message(
                [
                    self._build_image_block(image_source, mime_type=mime_type),
                    {"type": "text", "text": prompt},
                ]
            )
            return response
        except Exception as e:
            raise Exception(f"Error during Anthropic vision generation: {e}")

    async def generate_with_video(self, video_source, prompt: str, mime_type: Optional[str] = None) -> str:
        try:
            frame_blocks = self._build_video_frame_blocks(video_source, mime_type=mime_type)
            response = self._create_user_message(
                frame_blocks
                + [
                    {
                        "type": "text",
                        "text": (
                            "Assess the video from these sampled frames. "
                            "Audio is not included in this Anthropic request.\n\n"
                            f"{prompt}"
                        ),
                    }
                ]
            )
            return response
        except Exception as e:
            raise Exception(f"Error during Anthropic video generation: {e}")

    async def generate_with_audio(self, audio_source, prompt: str, mime_type: Optional[str] = None) -> str:
        transcript = None
        if isinstance(audio_source, dict):
            transcript = audio_source.get("transcript") or audio_source.get("text")

        if transcript:
            return self._create_user_message(
                [
                    {
                        "type": "text",
                        "text": f"{prompt}\n\nAudio transcript:\n{transcript}",
                    }
                ]
            )

        raise NotImplementedError(
            "Anthropic Messages API does not support raw audio input. "
            "Pass a transcript or use Gemini for native audio evaluation."
        )

    def _post_message(self, payload: Dict) -> AnthropicResponse:
        response = requests.post(
            self.API_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": self.ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        if not response.ok:
            raise Exception(f"Anthropic API error {response.status_code}: {response.text}")
        return AnthropicResponse(response.json())

    def _create_user_message(self, content: List[Dict]) -> AnthropicResponse:
        return self._post_message(
            {
                "model": self.model_name,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": content}],
            }
        )

    def _normalize_messages(self, messages: List[Dict]) -> tuple[str, List[Dict]]:
        system_parts = []
        normalized_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_parts.append(self._content_to_text(content))
                continue

            if role not in {"user", "assistant"}:
                role = "user"

            normalized_messages.append(
                {
                    "role": role,
                    "content": self._normalize_content(content),
                }
            )

        if not normalized_messages:
            normalized_messages.append({"role": "user", "content": ""})

        return "\n\n".join([part for part in system_parts if part]), normalized_messages

    def _normalize_content(self, content):
        if not isinstance(content, list):
            return str(content)

        normalized = []
        for item in content:
            if not isinstance(item, dict):
                normalized.append({"type": "text", "text": str(item)})
                continue

            item_type = item.get("type")
            if item_type == "text":
                normalized.append({"type": "text", "text": item.get("text", "")})
            elif item_type == "image" and item.get("source"):
                normalized.append(item)
            elif item_type == "image_url":
                image_url = item.get("image_url", {}).get("url")
                normalized.append(self._build_image_block(image_url))
            else:
                normalized.append(item)

        return normalized

    def _content_to_text(self, content) -> str:
        if not isinstance(content, list):
            return str(content)

        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            else:
                text_parts.append(str(item))
        return "\n".join([part for part in text_parts if part])

    def _build_image_block(self, image_source, mime_type: Optional[str] = None) -> Dict:
        if isinstance(image_source, dict):
            media_bytes = image_source.get("content")
            resolved_mime_type = mime_type or image_source.get("mime_type")
            if media_bytes is not None:
                return self._image_block_from_bytes(media_bytes, resolved_mime_type)

            local_path = image_source.get("local_path")
            if local_path and os.path.isfile(local_path):
                return self._image_block_from_file(local_path, resolved_mime_type)

            media_url = image_source.get("submission_url") or image_source.get("url")
            if media_url:
                return self._image_block_from_url(media_url)

            raise ValueError("Image source must contain content, local_path, submission_url, or url")

        if isinstance(image_source, (bytes, bytearray)):
            return self._image_block_from_bytes(image_source, mime_type)

        image_value = str(image_source)
        if os.path.isfile(image_value):
            return self._image_block_from_file(image_value, mime_type)
        return self._image_block_from_url(image_value)

    def _image_block_from_file(self, image_path: str, mime_type: Optional[str] = None) -> Dict:
        resolved_mime_type = mime_type or self._infer_mime_type(image_path)
        with open(image_path, "rb") as image_file:
            return self._image_block_from_bytes(image_file.read(), resolved_mime_type)

    def _image_block_from_bytes(self, image_bytes, mime_type: Optional[str]) -> Dict:
        image_bytes = bytes(image_bytes)
        resolved_mime_type = self._resolve_image_mime_type(image_bytes, mime_type)
        if resolved_mime_type not in self.SUPPORTED_IMAGE_MIME_TYPES:
            supported = ", ".join(sorted(self.SUPPORTED_IMAGE_MIME_TYPES))
            raise ValueError(f"Unsupported Anthropic image type '{resolved_mime_type}'. Use one of: {supported}")

        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": resolved_mime_type,
                "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
            },
        }

    def _resolve_image_mime_type(self, image_bytes: bytes, mime_type: Optional[str]) -> Optional[str]:
        detected_mime_type = detect_image_mime_type(image_bytes)
        if detected_mime_type:
            return detected_mime_type
        return self._normalize_mime_type(mime_type)

    def _image_block_from_url(self, image_url: str) -> Dict:
        normalized_url = self._normalize_media_url(image_url)
        parsed = urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Anthropic image URL sources must be HTTP(S) URLs")

        return {
            "type": "image",
            "source": {
                "type": "url",
                "url": normalized_url,
            },
        }

    def _build_video_frame_blocks(self, video_source, mime_type: Optional[str] = None) -> List[Dict]:
        import cv2

        video_path, cleanup_path = self._media_path_from_source(video_source, mime_type, ".mp4")
        cap = None
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise ValueError("Could not open video source")

            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            indexes = self._sample_frame_indexes(frame_count, self.DEFAULT_VIDEO_FRAME_COUNT)
            frame_blocks = []

            for frame_number, frame_index in enumerate(indexes, start=1):
                if frame_count > 0:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = cap.read()
                if not ok:
                    continue

                encoded_ok, buffer = cv2.imencode(".jpg", frame)
                if not encoded_ok:
                    continue

                frame_blocks.append(
                    {
                        "type": "text",
                        "text": f"Video frame {frame_number} of {len(indexes)}:",
                    }
                )
                frame_blocks.append(self._image_block_from_bytes(buffer.tobytes(), "image/jpeg"))

            if not frame_blocks:
                raise ValueError("Could not extract usable frames from video")

            return frame_blocks
        finally:
            if cap:
                cap.release()
            if cleanup_path and os.path.exists(cleanup_path):
                os.remove(cleanup_path)

    def _sample_frame_indexes(self, frame_count: int, max_frames: int) -> List[int]:
        if frame_count <= 0:
            return list(range(max_frames))

        count = min(max_frames, frame_count)
        if count == 1:
            return [0]

        return [round(index * (frame_count - 1) / (count - 1)) for index in range(count)]

    def _media_path_from_source(self, media_source, mime_type: Optional[str], default_suffix: str) -> tuple[str, Optional[str]]:
        if isinstance(media_source, dict):
            local_path = media_source.get("local_path")
            if local_path and os.path.isfile(local_path):
                return local_path, None

            media_bytes = media_source.get("content")
            if media_bytes is not None:
                resolved_mime_type = mime_type or media_source.get("mime_type")
                return self._write_temp_media(media_bytes, resolved_mime_type, default_suffix)

            media_url = media_source.get("submission_url") or media_source.get("url")
            if media_url:
                return self._download_media_to_temp(media_url, mime_type, default_suffix)

            raise ValueError("Media source must contain content, local_path, submission_url, or url")

        if isinstance(media_source, (bytes, bytearray)):
            return self._write_temp_media(media_source, mime_type, default_suffix)

        media_value = str(media_source)
        if os.path.isfile(media_value):
            return media_value, None
        return self._download_media_to_temp(media_value, mime_type, default_suffix)

    def _write_temp_media(self, media_bytes, mime_type: Optional[str], default_suffix: str) -> tuple[str, str]:
        suffix = self._suffix_for_mime_type(mime_type, default_suffix)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(bytes(media_bytes))
            return temp_file.name, temp_file.name

    def _download_media_to_temp(self, media_url: str, mime_type: Optional[str], default_suffix: str) -> tuple[str, str]:
        normalized_url = self._normalize_media_url(media_url)
        parsed = urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Anthropic provider can only download HTTP(S) media URLs")

        response = requests.get(normalized_url, timeout=120)
        if not response.ok:
            raise Exception(f"Failed to download media {response.status_code}: {response.text}")

        resolved_mime_type = mime_type or response.headers.get("content-type", "").split(";")[0] or self._infer_mime_type(normalized_url)
        return self._write_temp_media(response.content, resolved_mime_type, default_suffix)

    def _suffix_for_mime_type(self, mime_type: Optional[str], default_suffix: str) -> str:
        if not mime_type:
            return default_suffix
        return mimetypes.guess_extension(mime_type) or default_suffix

    def _infer_mime_type(self, source: str) -> str:
        parsed = urlparse(str(source))
        path = parsed.path or str(source)
        return self._normalize_mime_type(mimetypes.guess_type(path)[0]) or "application/octet-stream"

    def _normalize_mime_type(self, mime_type: Optional[str]) -> Optional[str]:
        if not mime_type:
            return None
        if mime_type == "image/jpg":
            return "image/jpeg"
        return mime_type

    def _normalize_media_url(self, media_url: str) -> str:
        media_url = str(media_url)
        if media_url.startswith("gs://"):
            return media_url.replace("gs://", "https://storage.googleapis.com/", 1)
        return media_url

    def calculate_cost(self, response):
        usage = response.get("usage") or {}
        model = (response.get("model") or self.model_name or "").lower()
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        input_rate, output_rate = self._pricing_for_model(model)
        cost = (input_tokens * (input_rate / 1_000_000)) + (output_tokens * (output_rate / 1_000_000))
        return round(cost, 6)

    def _pricing_for_model(self, model: str) -> tuple[float, float]:
        if "fable" in model or "mythos" in model:
            return 10.00, 50.00
        if "opus-5" in model or "opus-4-8" in model or "opus-4.8" in model or "opus-4-7" in model or "opus-4.7" in model:
            return 5.00, 25.00
        if "sonnet-5" in model:
            return 2.00, 10.00
        if "haiku-4-5" in model or "haiku-4.5" in model:
            return 1.00, 5.00
        if "haiku-3-5" in model or "haiku-3.5" in model:
            return 0.80, 4.00
        if "opus" in model:
            return 5.00, 25.00
        if "sonnet" in model:
            return 3.00, 15.00
        return 3.00, 15.00


class GeminiProvider(BaseLLMInterface):
    """Gemini provider using Vertex AI."""

    _vertex_init = {"key_data_id": None, "project_id": None, "location": None}

    def __init__(
        self,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        settings: Any = None,
    ):
        super().__init__(api_key, model_name, temperature, max_tokens)
        
        self.key_data = self._resolve_service_account_credentials(settings)
        self.location = settings.location
        self.project_id = settings.project_id
        self._ensure_vertex_init()

    def _resolve_service_account_credentials(self, settings: Any) -> Optional[Dict]:
        raw_key = settings.get("credentials_json")

        if isinstance(raw_key, dict):
            return raw_key
        if isinstance(raw_key, str):
            raw_key = raw_key.strip()
            if raw_key:
                try:
                    return json.loads(raw_key)
                except json.JSONDecodeError:
                    return None

        return None

    def _key_data_id(self) -> Optional[tuple]:
        if not self.key_data:
            return None
        return (
            self.key_data.get("project_id"),
            self.key_data.get("client_email"),
            self.key_data.get("private_key_id"),
        )

    def _ensure_vertex_init(self) -> None:
        init_state = GeminiProvider._vertex_init
        if not self.key_data:
            raise ValueError("Gemini service account key JSON is required")

        key_data_id = self._key_data_id()
        resolved_project_id = self.project_id or self.key_data.get("project_id")
        if (
            init_state["key_data_id"] == key_data_id
            and init_state["project_id"] == resolved_project_id
            and init_state["location"] == self.location
        ):
            return

        credentials = service_account.Credentials.from_service_account_info(self.key_data)
        if not resolved_project_id:
            raise ValueError("Project ID is required for Gemini provider initialization")

        vertexai.init(project=resolved_project_id, location=self.location, credentials=credentials)
        GeminiProvider._vertex_init = {
            "key_data_id": key_data_id,
            "project_id": resolved_project_id,
            "location": self.location,
        }

    def _combine_messages(self, messages: List[Dict]) -> str:
        parts = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
            else:
                parts.append(str(content))
        return "\n\n".join([p for p in parts if p])

    async def generate(self, messages: List[Dict]) -> str:
        prompt = self._combine_messages(messages)
        model = GenerativeModel(self.model_name)
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": self.temperature,
            },
        )
        cost = self.calculate_cost(response.to_dict())
        return response.text, cost or "", 0.0

    def _build_media_part(self, media_source, mime_type: Optional[str] = None, default_kind: str = "application") -> Part:
        if isinstance(media_source, dict):
            media_bytes = media_source.get("content")
            resolved_mime_type = mime_type or media_source.get("mime_type")
            if media_bytes is not None:
                return Part.from_data(data=media_bytes, mime_type=resolved_mime_type or f"{default_kind}/octet-stream")
            media_url = media_source.get("submission_url") or media_source.get("url")
            resolved_mime_type = resolved_mime_type or self._infer_mime_type(media_url)
            return Part.from_uri(uri=self._normalize_media_uri(media_url), mime_type=resolved_mime_type)

        if isinstance(media_source, (bytes, bytearray)):
            return Part.from_data(data=bytes(media_source), mime_type=mime_type or f"{default_kind}/octet-stream")

        media_url = str(media_source)
        return Part.from_uri(
            uri=self._normalize_media_uri(media_url),
            mime_type=mime_type or self._infer_mime_type(media_url),
        )

    async def _generate_with_media(self, media_source, prompt: str, mime_type: Optional[str] = None, default_kind: str = "application") -> str:
        media_part = self._build_media_part(media_source, mime_type=mime_type, default_kind=default_kind)
        model = GenerativeModel(self.model_name)
        response = model.generate_content(
            [media_part, prompt],
            generation_config={
                "temperature": self.temperature,
                "response_mime_type": "application/json",
            },
        )
        return response

    async def generate_with_vision(self, image_source, prompt: str, mime_type: Optional[str] = None) -> str:
        try:
            return await self._generate_with_media(
                image_source,
                prompt,
                mime_type=mime_type,
                default_kind="image",
            )
        except Exception as e:
            raise Exception(f"Error during vision generation: {e}")


    async def generate_with_video(self, video_source, prompt: str, mime_type: Optional[str] = None) -> str:
        try:
            return await self._generate_with_media(
                video_source,
                prompt,
                mime_type=mime_type,
                default_kind="video",
            )
        except Exception as e:
            print(f"Error during video generation: {e}")
            raise Exception(f"Error during video generation: {e}")

    async def generate_with_audio(self, audio_source, prompt: str, mime_type: Optional[str] = None) -> str:
        try:
            return await self._generate_with_media(
                audio_source,
                prompt,
                mime_type=mime_type,
                default_kind="audio",
            )
        except Exception as e:
            print(f"Error during audio generation: {e}")
            raise Exception(f"Error during audio generation: {e}")
        
    def _infer_mime_type(self, url: str) -> str:
        if url.endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif")):
            return f"image/{url.split('.')[-1]}"
        if url.endswith((".mp4", ".avi", ".mov")):
            return f"video/{url.split('.')[-1]}"
        if url.endswith((".mp3", ".wav", ".ogg", ".aac", ".m4a", ".flac")):
            extension = url.split(".")[-1]
            if extension == "mp3":
                return "audio/mpeg"
            if extension == "m4a":
                return "audio/mp4"
            return f"audio/{extension}"
        return "application/octet-stream"

    def _normalize_media_uri(self, media_url: str) -> str:
        if media_url.startswith("https://storage.googleapis.com/"):
            return media_url.replace("https://storage.googleapis.com/", "gs://", 1)
        return media_url

    def calculate_cost(self, response):
        """
        Calculates the cost of a Gemini API call based on usage_metadata.
        Supports Gemini 2.5 Pro (tiered), 2.5 Flash, and 3.1 Pro/Flash.
        """
        metadata = response.get("usage_metadata")
        model = response.get("model_version", "gemini-2.5-pro")
        
        
        # Token counts
        # For images/multimodal, 'total_token_count' includes the media tokens
        output_tokens = metadata.get("candidates_token_count", 0)
        input_tokens = metadata.get("total_token_count", 0) - output_tokens
        
        # Pricing per 1 Million Tokens (as of March 2026)
        pricing = {
            "gemini-2.5-pro": {
                "input_std": 1.25, "output_std": 10.00,
                "input_long": 2.50, "output_long": 15.00
            },
            "gemini-3.1-pro": {
                "input_std": 2.00, "output_std": 12.00,
                "input_long": 4.00, "output_long": 18.00
            },
            "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
            "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
            "gemini-3-flash": {"input": 0.50, "output": 3.00}
        }

        # Determine rate based on model and context length
        if "2.5-pro" in model or "3.1-pro" in model:
            base_model = "gemini-2.5-pro" if "2.5" in model else "gemini-3.1-pro"
            # 200k token threshold for tiered pricing
            if input_tokens <= 200_000:
                in_rate = pricing[base_model]["input_std"]
                out_rate = pricing[base_model]["output_std"]
            else:
                in_rate = pricing[base_model]["input_long"]
                out_rate = pricing[base_model]["output_long"]
        else:
            # Flash models usually have flat pricing
            rates = pricing.get(model, pricing["gemini-2.5-flash"]) # Default to Flash
            in_rate = rates["input"]
            out_rate = rates["output"]

        # Calculate final cost
        cost = (input_tokens * (in_rate / 1_000_000)) + (output_tokens * (out_rate / 1_000_000))
        return round(cost, 6)

def create_llm_provider(
    provider: str,
    api_key: str,
    model_name: str,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    **kwargs,
) -> BaseLLMInterface:
    """Factory function to create LLM provider instances"""
    
    providers = {
        "OpenAI": OpenAIProvider,
        "Anthropic": AnthropicProvider,
        "Together AI": TogetherAIProvider,
        "Gemini": GeminiProvider,
    }
    
    if provider not in providers:
        raise ValueError(f"Unsupported provider: {provider}")
    
    return providers[provider](api_key, model_name, temperature, max_tokens, **kwargs)
