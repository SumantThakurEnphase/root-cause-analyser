"""
Gemini client — wraps the Google Generative AI SDK for RCA analysis.
"""

import google.generativeai as genai
from config import config


class GeminiClient:
    def __init__(self):
        genai.configure(api_key=config.GEMINI_API_KEY)
        self._model = genai.GenerativeModel(config.GEMINI_MODEL)

    async def analyze(self, prompt: str) -> str:
        """
        Send a prompt to Gemini and return the response text.

        Uses generate_content (sync under the hood for google-generativeai SDK).
        """
        try:
            response = self._model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=4096,
                ),
            )
            return response.text
        except Exception as e:
            return f"⚠️ Gemini API error: {e}"
