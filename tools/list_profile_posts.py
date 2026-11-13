from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from socialscrape_api import POST_PLATFORMS, SocialScrapeClient, bare_handle, pick_platform


class ListProfilePostsTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        platform = pick_platform(tool_parameters.get("platform"), POST_PLATFORMS)
        handle = bare_handle(tool_parameters.get("handle"))
        cursor = str(tool_parameters.get("cursor") or "").strip() or None
        with SocialScrapeClient(self.runtime.credentials.get("api_key")) as client:
            yield self.create_json_message(client.profile_posts(platform, handle, cursor))
