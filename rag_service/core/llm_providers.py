# rag_service/rag_service/core/llm_providers.py

import aiohttp
import asyncio
import json
from typing import List, Dict, Optional
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
from .llm_interface import BaseLLMInterface
import vertexai
from google.oauth2 import service_account
from vertexai.generative_models import GenerativeModel, Part

class OpenAIProvider(BaseLLMInterface):
    """OpenAI provider using LangChain"""
    
    def __init__(self, api_key: str, model_name: str, temperature: float = 0.7, max_tokens: int = 2000):
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
    
    def __init__(self, api_key: str, model_name: str, temperature: float = 0.7, max_tokens: int = 2000):
        super().__init__(api_key, model_name, temperature, max_tokens)
        self.base_url = "https://api.together.xyz/v1/chat/completions"
        
        # Llama 3.2 90B specific configurations
        self.is_llama_32_90b = "Llama-3.2-90B" in model_name
        if self.is_llama_32_90b:
            # Optimize for Llama 3.2 90B
            self.timeout = 90  # Longer timeout for larger model
            self.max_retries = 3
    
    async def generate(self, messages: List[Dict]) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            # Llama 3.2 90B specific parameters
            "top_p": 0.95,
            "repetition_penalty": 1.1,
            "stream": False
        }
        
        # Add specific parameters for Llama 3.2 90B
        if self.is_llama_32_90b:
            data.update({
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
                "stop": ["</s>", "<|eot_id|>"]  # Llama 3.2 stop tokens
            })
        
        timeout = aiohttp.ClientTimeout(total=self.timeout if hasattr(self, 'timeout') else 60)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(self.max_retries if hasattr(self, 'max_retries') else 1):
                try:
                    async with session.post(self.base_url, headers=headers, json=data) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            if attempt < (self.max_retries - 1) if hasattr(self, 'max_retries') else 0:
                                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                                continue
                            raise Exception(f"Together AI API error: {response.status} - {error_text}")
                        
                        result = await response.json()
                        return result["choices"][0]["message"]["content"]
                except asyncio.TimeoutError:
                    if attempt < (self.max_retries - 1) if hasattr(self, 'max_retries') else 0:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise Exception("Request timeout - Llama 3.2 90B may need more processing time")
    
    async def generate_with_vision(self, messages: List[Dict]) -> str:
        # Llama 3.2 90B Vision handles image URLs in the message content
        return await self.generate(messages)
    
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
                            "url": image_url,
                            "detail": "high"  # Llama 3.2 90B can handle high detail
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


class GeminiProvider(BaseLLMInterface):
    """Gemini provider using Vertex AI."""

    _vertex_init = {"key_data_id": None, "project_id": None, "location": None}

    def __init__(
        self,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        key_data: Optional[Dict] = None,
        location: str = "us-central1",
        project_id: Optional[str] = None,
    ):
        super().__init__(api_key, model_name, temperature, max_tokens)
        self.key_data = self._normalize_key_data(key_data)
        self.location = location
        self.project_id = project_id
        self._ensure_vertex_init()

    def _normalize_key_data(self, key_data: Optional[Dict]) -> Optional[Dict]:
        if not key_data:
            return None
        if isinstance(key_data, dict):
            return key_data
        if isinstance(key_data, str):
            try:
                return json.loads(key_data)
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
        return response.text or ""

    async def generate_with_vision(self, messages: List[Dict]) -> str:
        prompt = self._combine_messages(messages)
        image_url = None
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        image_url = item.get("image_url", {}).get("url")
                        break
            if image_url:
                break

        if not image_url:
            return await self.generate(messages)

        image_part = Part.from_uri(uri=image_url, mime_type="image/jpeg")
        model = GenerativeModel(self.model_name)
        response = model.generate_content(
            [image_part, prompt],
            generation_config={
                "temperature": self.temperature,
            },
        )
        return response.text or ""

    async def generate_with_video(self, video_url: str, prompt: str) -> str:
        video_uri = self._normalize_video_uri(video_url)
        video_part = Part.from_uri(uri=video_uri, mime_type="video/mp4")
        model = GenerativeModel(self.model_name)
        response = model.generate_content(
            [video_part, prompt],
            generation_config={
                "temperature": self.temperature,
                "response_mime_type": "application/json",
            },
        )
        return response.text or ""

    def _normalize_video_uri(self, video_url: str) -> str:
        if video_url.startswith("https://storage.googleapis.com/"):
            return video_url.replace("https://storage.googleapis.com/", "gs://", 1)
        return video_url


# Factory function to create LLM providers
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
    }
    
    if provider not in providers:
        raise ValueError(f"Unsupported provider: {provider}")
    
    return providers[provider](api_key, model_name, temperature, max_tokens, **kwargs)
