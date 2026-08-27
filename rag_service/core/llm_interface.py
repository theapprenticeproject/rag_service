# rag_service/rag_service/core/llm_interface.py

from abc import ABC, abstractmethod
from typing import List, Dict, Optional

class BaseLLMInterface(ABC):
    """Base interface for all LLM providers"""
    
    def __init__(self, api_key: str, model_name: str, temperature: float = 0.7, max_tokens: int = 2000):
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
    
    @abstractmethod
    async def generate(self, messages: List[Dict]) -> str:
        """Generate response from LLM"""
        pass
    
    @abstractmethod
    async def generate_with_vision(self, messages: List[Dict]) -> str:
        """Generate response from vision-enabled LLM"""
        pass

    async def generate_with_video(self, video_source, prompt: str, mime_type: Optional[str] = None):
        raise NotImplementedError("Video generation is not supported by this provider")

    async def generate_with_audio(self, audio_source, prompt: str, mime_type: Optional[str] = None):
        raise NotImplementedError("Audio generation is not supported by this provider")
    
    def format_messages(self, system_prompt: str, user_prompt: str, image_url: Optional[str] = None) -> List[Dict]:
        """Format messages for the specific LLM provider"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        if image_url:
            # This is a basic format, each provider might need different formatting
            messages[-1]["content"] = [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
            
        return messages
