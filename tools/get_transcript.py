from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from socialscrape_api import SocialScrapeClient


class GetTranscriptTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        url = str(tool_parameters.get("url") or "").strip()
        if not url:
            raise ValueError("url is required: the link to a video on YouTube, TikTok, Instagram, X or another supported platform.")
        language = str(tool_parameters.get("language") or "").strip() or None
        timestamps = bool(tool_parameters.get("timestamps", False))
        allow_long = bool(tool_parameters.get("allow_long_transcription", False))
        with SocialScrapeClient(self.runtime.credentials.get("api_key")) as client:
            result = client.transcript(url, language, timestamps, allow_long)
        yield self.create_json_message(result)
        text = result.get("text")
        if isinstance(text, str) and text:
            yield self.create_variable_message("text", text)
