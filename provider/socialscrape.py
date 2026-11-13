from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from socialscrape_api import SocialScrapeClient, SocialScrapeError


class SocialScrapeProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            with SocialScrapeClient(credentials.get("api_key")) as client:
                client.credits()
        except SocialScrapeError as exc:
            raise ToolProviderCredentialValidationError(str(exc)) from None
