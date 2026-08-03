# rag_service/rag_service/core/llm_providers.py

import asyncio
import json
from typing import Any, Dict, List, Optional

import aiohttp
import vertexai
from google.oauth2 import service_account
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from together import Together
from vertexai.generative_models import GenerativeModel, Part

from .llm_interface import BaseLLMInterface


class StubResponse:
    """Mock Gemini response object for the StubProvider"""

    def __init__(self, text, usage_metadata=None):
        self.text = text
        self._usage_metadata = usage_metadata or {
            "candidates_token_count": 100,
            "total_token_count": 500,
        }

    def to_dict(self):
        return {
            "candidates": [{"avg_logprobs": -0.5}],
            "usage_metadata": self._usage_metadata,
            "model_version": "stub-model",
        }


class StubProvider(BaseLLMInterface):
    """Stub provider for local testing without real LLM costs"""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        settings: Any = None,
    ):
        super().__init__(api_key, model_name, temperature, max_tokens)
        self.base_url = getattr(settings, "base_url", "http://llm-stub:8001")

    async def _simulate_latency(self):
        await asyncio.sleep(1.5)

    async def _call_stub(self, prompt: str) -> StubResponse:
        await self._simulate_latency()

        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data["choices"][0]["message"]["content"]
                        return StubResponse(content)
                    else:
                        error_text = await resp.text()
                        print(f"Stub Error ({resp.status}): {error_text}")
        except Exception as e:
            print(f"Connection to LLM stub failed: {e}")

        return StubResponse(
            '{"overall_feedback": "Stub error fallback", "final_grade": 0, "rubric_evaluations": []}'
        )

    async def generate(self, messages: List[Dict]) -> Any:
        prompt = str(messages)
        return await self._call_stub(prompt)

    async def generate_with_vision(
        self, image_source, prompt: str, mime_type: Optional[str] = None
    ) -> Any:
        return await self._call_stub(prompt)

    async def generate_with_video(
        self, video_source, prompt: str, mime_type: Optional[str] = None
    ) -> Any:
        return await self._call_stub(prompt)

    async def generate_with_audio(
        self, audio_source, prompt: str, mime_type: Optional[str] = None
    ) -> Any:
        return await self._call_stub(prompt)

    def calculate_cost(self, response_dict: Dict) -> float:
        return 0.00123


class OpenAIProvider(BaseLLMInterface):
    """OpenAI provider using LangChain"""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        settings: Any = None,
    ):
        super().__init__(api_key, model_name, temperature, max_tokens)
        self.llm = ChatOpenAI(
            model_name=model_name,
            openai_api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
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

    def __init__(
        self,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 15000,
        settings: Any = None,
    ):
        super().__init__(api_key, model_name, temperature, max_tokens)
        self.client = Together(api_key=api_key)

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            messages = self.format_messages(system_prompt, user_prompt)
            response = await self.client.chat.completions.create(
                model=self.model_name, messages=messages, temperature=self.temperature
            )
            return response.text
        except Exception as e:
            print(f"Error during Together AI generation: {e}")
            raise Exception(f"Error during Together AI generation: {e}")

    async def generate_with_vision(
        self, image_url: str, system_prompt: str, user_prompt: str = ""
    ) -> str:
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

    def format_messages(
        self, system_prompt: str, user_prompt: str, image_url: Optional[str] = None
    ) -> List[Dict]:
        """Format messages specifically for Llama 3.2 90B Vision"""
        messages = []

        # Llama 3.2 90B sometimes performs better with system prompts in user messages
        combined_prompt = f"{system_prompt}\n\n{user_prompt}"

        if image_url:
            # Llama 3.2 90B Vision expects this specific format
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": combined_prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            )
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
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

        credentials = service_account.Credentials.from_service_account_info(
            self.key_data
        )
        if not resolved_project_id:
            raise ValueError(
                "Project ID is required for Gemini provider initialization"
            )

        vertexai.init(
            project=resolved_project_id, location=self.location, credentials=credentials
        )
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

    def _build_media_part(
        self,
        media_source,
        mime_type: Optional[str] = None,
        default_kind: str = "application",
    ) -> Part:
        if isinstance(media_source, dict):
            media_bytes = media_source.get("content")
            resolved_mime_type = mime_type or media_source.get("mime_type")
            if media_bytes is not None:
                return Part.from_data(
                    data=media_bytes,
                    mime_type=resolved_mime_type or f"{default_kind}/octet-stream",
                )
            media_url = media_source.get("submission_url") or media_source.get("url")
            resolved_mime_type = resolved_mime_type or self._infer_mime_type(media_url)
            return Part.from_uri(
                uri=self._normalize_media_uri(media_url), mime_type=resolved_mime_type
            )

        if isinstance(media_source, (bytes, bytearray)):
            return Part.from_data(
                data=bytes(media_source),
                mime_type=mime_type or f"{default_kind}/octet-stream",
            )

        media_url = str(media_source)
        return Part.from_uri(
            uri=self._normalize_media_uri(media_url),
            mime_type=mime_type or self._infer_mime_type(media_url),
        )

    async def _generate_with_media(
        self,
        media_source,
        prompt: str,
        mime_type: Optional[str] = None,
        default_kind: str = "application",
    ) -> str:
        media_part = self._build_media_part(
            media_source, mime_type=mime_type, default_kind=default_kind
        )
        model = GenerativeModel(self.model_name)
        response = model.generate_content(
            [media_part, prompt],
            generation_config={
                "temperature": self.temperature,
                "response_mime_type": "application/json",
            },
        )
        return response

    async def generate_with_vision(
        self, image_source, prompt: str, mime_type: Optional[str] = None
    ) -> str:
        try:
            return await self._generate_with_media(
                image_source,
                prompt,
                mime_type=mime_type,
                default_kind="image",
            )
        except Exception as e:
            raise Exception(f"Error during vision generation: {e}")

    async def generate_with_video(
        self, video_source, prompt: str, mime_type: Optional[str] = None
    ) -> str:
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

    async def generate_with_audio(
        self, audio_source, prompt: str, mime_type: Optional[str] = None
    ) -> str:
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
                "input_std": 1.25,
                "output_std": 10.00,
                "input_long": 2.50,
                "output_long": 15.00,
            },
            "gemini-3.1-pro": {
                "input_std": 2.00,
                "output_std": 12.00,
                "input_long": 4.00,
                "output_long": 18.00,
            },
            "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
            "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
            "gemini-3-flash": {"input": 0.50, "output": 3.00},
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
            rates = pricing.get(model, pricing["gemini-2.5-flash"])  # Default to Flash
            in_rate = rates["input"]
            out_rate = rates["output"]

        # Calculate final cost
        cost = (input_tokens * (in_rate / 1_000_000)) + (
            output_tokens * (out_rate / 1_000_000)
        )
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
        "Together AI": TogetherAIProvider,
        "Gemini": GeminiProvider,
        "Stub": StubProvider,
    }

    if provider not in providers:
        raise ValueError(f"Unsupported provider: {provider}")

    return providers[provider](api_key, model_name, temperature, max_tokens, **kwargs)
