# rag_service/rag_service/core/llm_providers.py

import aiohttp
import asyncio
from typing import List, Dict, Optional
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
from .llm_interface import BaseLLMInterface

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


# Factory function to create LLM providers
def create_llm_provider(provider: str, api_key: str, model_name: str, 
                      temperature: float = 0.7, max_tokens: int = 2000) -> BaseLLMInterface:
    """Factory function to create LLM provider instances"""
    
    providers = {
        "OpenAI": OpenAIProvider,
        "Together AI": TogetherAIProvider,
    }
    
    if provider not in providers:
        raise ValueError(f"Unsupported provider: {provider}")
    
    return providers[provider](api_key, model_name, temperature, max_tokens)
