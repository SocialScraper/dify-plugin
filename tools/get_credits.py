from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from socialscrape_api import SocialScrapeClient


class GetCreditsTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        with SocialScrapeClient(self.runtime.credentials.get("api_key")) as client:
            yield self.create_json_message(client.credits())
