"""Tool and provider tests through the real dify_plugin classes, with mocked HTTP.

Run from the plugin directory: python -m unittest discover -s tests
Set SOCIALSCRAPE_LIVE_KEY to also run the live tests against the real API.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dify_plugin.entities.tool import ToolInvokeMessage, ToolRuntime  # noqa: E402
from dify_plugin.errors.tool import ToolProviderCredentialValidationError  # noqa: E402

import socialscrape_api  # noqa: E402
from provider.socialscrape import SocialScrapeProvider  # noqa: E402
from tools.get_credits import GetCreditsTool  # noqa: E402
from tools.get_profile import GetProfileTool  # noqa: E402
from tools.get_transcript import GetTranscriptTool  # noqa: E402
from tools.list_profile_posts import ListProfilePostsTool  # noqa: E402

KEY = "ss_live_test_key"


def make_tool(cls, key=KEY):
    return cls(runtime=ToolRuntime(credentials={"api_key": key}, user_id="u", session_id="s"), session=mock.MagicMock())


def messages(tool, params):
    return list(tool.invoke(params))


def json_of(msgs):
    found = [m.message.json_object for m in msgs if m.type == ToolInvokeMessage.MessageType.JSON]
    assert len(found) == 1, msgs
    return found[0]


def ok(data, charged=1, remaining=99, cached=False):
    """A SocialScrape success envelope around one data object."""
    return {"ok": True, "credits_charged": charged, "credits_remaining": remaining, "cached": cached,
            "cached_at": None, "request_id": "req_1", "data": data}


def fail(code, message):
    return {"ok": False, "error": {"code": code, "message": message, "retryable": False},
            "credits_charged": 0, "request_id": "req_2"}


class Recorder:
    """A mocked API: routes (method, path) to a list of responses, records requests."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        queue = self.routes.get(key)
        if not queue:
            return httpx.Response(404, json=fail("not_found", f"no route {key}"))
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=body)


class MockedApi:
    def __init__(self, routes):
        self.recorder = Recorder(routes)
        real = httpx.Client

        def client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(self.recorder)
            return real(*args, **kwargs)

        self._patches = [
            mock.patch.object(socialscrape_api.httpx, "Client", side_effect=client),
            mock.patch.object(socialscrape_api.time, "sleep", lambda s: None),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self.recorder

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


CREDITS = ok({"object": "credit_balance", "credits_remaining": 99, "team_id": "team_1"}, charged=0)
TRANSCRIPT = ok({
    "object": "transcript", "platform": "youtube", "video_id": "jNQXAC9IVRw",
    "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "language": "en", "source": "captions",
    "duration_seconds": 19, "text": "All right, so here we are",
    "segments": [{"start": 0, "end": 2, "text": "All right, so here we are"}],
})
JOB = ok({"object": "job", "job_id": "job_1", "status": "queued", "poll_url": "/v1/jobs/job_1"}, charged=0)


class ProviderTests(unittest.TestCase):
    def test_valid_key_reads_the_credit_balance_with_the_key_header(self):
        with MockedApi({("GET", "/v1/account/credits"): [(200, CREDITS)]}) as api:
            SocialScrapeProvider().validate_credentials({"api_key": KEY})
        req = api.requests[0]
        self.assertEqual(str(req.url), "https://api.socialscrape.dev/v1/account/credits")
        self.assertEqual(req.headers["x-api-key"], KEY)
        self.assertNotIn("authorization", req.headers)

    def test_bad_key_raises_without_leaking_it(self):
        with MockedApi({("GET", "/v1/account/credits"): [(401, fail("unauthorized", "this API key is not valid"))]}):
            with self.assertRaises(ToolProviderCredentialValidationError) as ctx:
                SocialScrapeProvider().validate_credentials({"api_key": KEY})
        self.assertIn("unauthorized", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))

    def test_empty_key_is_refused_before_any_request(self):
        with MockedApi({}) as api:
            with self.assertRaises(ToolProviderCredentialValidationError):
                SocialScrapeProvider().validate_credentials({"api_key": " "})
        self.assertEqual(api.requests, [])


class GetTranscriptTests(unittest.TestCase):
    def test_transcript_with_text_variable_and_no_segments_by_default(self):
        with MockedApi({("GET", "/v1/transcript"): [(200, TRANSCRIPT)]}) as api:
            msgs = messages(make_tool(GetTranscriptTool), {"url": " https://www.youtube.com/watch?v=jNQXAC9IVRw "})
        params = dict(api.requests[0].url.params)
        self.assertEqual(params, {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"})
        result = json_of(msgs)
        self.assertEqual(result["platform"], "youtube")
        self.assertNotIn("segments", result)
        self.assertEqual(result["usage"], {"credits_charged": 1, "credits_remaining": 99, "cached": False})
        variables = [m for m in msgs if m.type == ToolInvokeMessage.MessageType.VARIABLE]
        self.assertEqual(variables[0].message.variable_name, "text")

    def test_timestamps_and_language_are_passed_on(self):
        with MockedApi({("GET", "/v1/transcript"): [(200, TRANSCRIPT)]}) as api:
            result = json_of(messages(make_tool(GetTranscriptTool), {"url": "https://x.test/v", "language": "es", "timestamps": True}))
        self.assertEqual(api.requests[0].url.params["language"], "es")
        self.assertNotIn("allow_long_transcription", api.requests[0].url.params)
        self.assertEqual(len(result["segments"]), 1)

    def test_long_transcription_job_is_polled_until_done(self):
        with MockedApi({("GET", "/v1/transcript"): [(202, JOB)],
                        ("GET", "/v1/jobs/job_1"): [(200, JOB), (200, TRANSCRIPT)]}) as api:
            result = json_of(messages(make_tool(GetTranscriptTool), {"url": "https://x.test/v", "allow_long_transcription": True}))
        self.assertEqual(api.requests[0].url.params["allow_long_transcription"], "true")
        self.assertEqual(result["text"], "All right, so here we are")
        self.assertEqual(len([r for r in api.requests if r.url.path == "/v1/jobs/job_1"]), 2)

    def test_job_still_running_returns_the_job_id(self):
        ticks = iter(range(0, 10_000, 30))
        with mock.patch.object(socialscrape_api.time, "monotonic", lambda: next(ticks)):
            with MockedApi({("GET", "/v1/transcript"): [(202, JOB)], ("GET", "/v1/jobs/job_1"): [(200, JOB)]}):
                result = json_of(messages(make_tool(GetTranscriptTool), {"url": "https://x.test/v", "allow_long_transcription": True}))
        self.assertEqual(result["status"], "processing")
        self.assertEqual(result["job_id"], "job_1")
        self.assertIn("again", result["message"])

    def test_failed_job_raises_its_error(self):
        with MockedApi({("GET", "/v1/transcript"): [(202, JOB)],
                        ("GET", "/v1/jobs/job_1"): [(422, fail("transcript_unavailable", "The audio has no speech."))]}):
            with self.assertRaises(ValueError) as ctx:
                messages(make_tool(GetTranscriptTool), {"url": "https://x.test/v", "allow_long_transcription": True})
        self.assertIn("transcript_unavailable", str(ctx.exception))

    def test_api_error_carries_code_and_message(self):
        body = fail("invalid_params", "This video is 12 minutes long: pass allow_long_transcription (60 credits).")
        with MockedApi({("GET", "/v1/transcript"): [(400, body)]}):
            with self.assertRaises(ValueError) as ctx:
                messages(make_tool(GetTranscriptTool), {"url": "https://x.test/v"})
        self.assertIn("invalid_params", str(ctx.exception))
        self.assertIn("60 credits", str(ctx.exception))

    def test_missing_url_is_refused(self):
        with MockedApi({}) as api:
            with self.assertRaises(ValueError):
                messages(make_tool(GetTranscriptTool), {"url": "  "})
        self.assertEqual(api.requests, [])

    def test_redirects_are_not_followed(self):
        with MockedApi({("GET", "/v1/transcript"): [(302, {"ok": True})]}) as api:
            with self.assertRaises(ValueError):
                messages(make_tool(GetTranscriptTool), {"url": "https://x.test/v"})
        self.assertEqual(len(api.requests), 1)


class ProfileTests(unittest.TestCase):
    PROFILE = ok({"object": "profile", "platform": "instagram", "handle": "nasa", "name": "NASA",
                  "stats": {"followers": 10, "following": 1, "posts": 2}})
    LIST = ok({"object": "list", "items": [{"object": "post", "platform": "tiktok", "id": "1"}],
               "cursor": "c2", "has_more": True})

    def test_profile_strips_the_at_sign_and_accepts_x_for_twitter(self):
        with MockedApi({("GET", "/v1/instagram/profile"): [(200, self.PROFILE)],
                        ("GET", "/v1/twitter/profile"): [(200, self.PROFILE)]}) as api:
            result = json_of(messages(make_tool(GetProfileTool), {"platform": "instagram", "handle": " @nasa "}))
            messages(make_tool(GetProfileTool), {"platform": "X", "handle": "nasa"})
        self.assertEqual(dict(api.requests[0].url.params), {"handle": "nasa"})
        self.assertEqual(api.requests[1].url.path, "/v1/twitter/profile")
        self.assertEqual(result["stats"]["followers"], 10)
        self.assertEqual(result["usage"]["credits_charged"], 1)

    def test_unknown_platform_and_empty_handle_are_refused_before_any_request(self):
        with MockedApi({}) as api:
            with self.assertRaises(ValueError):
                messages(make_tool(GetProfileTool), {"platform": "../account", "handle": "nasa"})
            with self.assertRaises(ValueError):
                messages(make_tool(GetProfileTool), {"platform": "instagram", "handle": "@"})
            with self.assertRaises(ValueError):
                messages(make_tool(ListProfilePostsTool), {"platform": "snapchat", "handle": "nasa"})
        self.assertEqual(api.requests, [])

    def test_tiktok_posts_use_the_videos_route_and_pass_the_cursor(self):
        with MockedApi({("GET", "/v1/tiktok/profile/videos"): [(200, self.LIST)]}) as api:
            result = json_of(messages(make_tool(ListProfilePostsTool), {"platform": "tiktok", "handle": "nasa", "cursor": "c1"}))
        self.assertEqual(dict(api.requests[0].url.params), {"handle": "nasa", "cursor": "c1"})
        self.assertEqual(result["posts"][0]["id"], "1")
        self.assertEqual(result["next_cursor"], "c2")
        self.assertIs(result["has_more"], True)

    def test_other_platforms_use_the_posts_route_without_a_cursor(self):
        empty = ok({"object": "list", "items": [], "cursor": None, "has_more": False})
        with MockedApi({("GET", "/v1/threads/profile/posts"): [(200, empty)]}) as api:
            result = json_of(messages(make_tool(ListProfilePostsTool), {"platform": "threads", "handle": "zuck"}))
        self.assertEqual(dict(api.requests[0].url.params), {"handle": "zuck"})
        self.assertEqual(result["posts"], [])
        self.assertIsNone(result["next_cursor"])
        self.assertIs(result["has_more"], False)

    def test_credits(self):
        with MockedApi({("GET", "/v1/account/credits"): [(200, CREDITS)]}):
            self.assertEqual(json_of(messages(make_tool(GetCreditsTool), {})), {"credits_remaining": 99, "team_id": "team_1"})

    def test_out_of_credits_is_reported_with_the_api_message(self):
        with MockedApi({("GET", "/v1/instagram/profile"): [(402, fail("insufficient_credits", "this team has no credits left"))]}):
            with self.assertRaises(ValueError) as ctx:
                messages(make_tool(GetProfileTool), {"platform": "instagram", "handle": "nasa"})
        self.assertIn("no credits left", str(ctx.exception))

    def test_timeout_is_reported_plainly(self):
        def boom(request):
            raise httpx.ReadTimeout("slow", request=request)

        real = httpx.Client
        with mock.patch.object(socialscrape_api.httpx, "Client",
                               side_effect=lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(boom)})):
            with self.assertRaises(ValueError) as ctx:
                messages(make_tool(GetCreditsTool), {})
        self.assertIn("did not answer in time", str(ctx.exception))


@unittest.skipUnless(os.environ.get("SOCIALSCRAPE_LIVE_KEY"), "set SOCIALSCRAPE_LIVE_KEY for live tests")
class LiveTests(unittest.TestCase):
    def setUp(self):
        self.key = os.environ["SOCIALSCRAPE_LIVE_KEY"]

    def test_live_provider_and_tools(self):
        SocialScrapeProvider().validate_credentials({"api_key": self.key})
        credits = json_of(messages(make_tool(GetCreditsTool, self.key), {}))
        self.assertIsInstance(credits["credits_remaining"], int)
        t = json_of(messages(make_tool(GetTranscriptTool, self.key), {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}))
        self.assertEqual(t["video_id"], "jNQXAC9IVRw")
        self.assertTrue(t["text"])
        p = json_of(messages(make_tool(GetProfileTool, self.key), {"platform": "instagram", "handle": "@nasa"}))
        self.assertEqual(p["handle"], "nasa")
        self.assertIsInstance(p["stats"]["followers"], int)
        posts = json_of(messages(make_tool(ListProfilePostsTool, self.key), {"platform": "tiktok", "handle": "nasa"}))
        self.assertTrue(posts["posts"])
        with self.assertRaises(ToolProviderCredentialValidationError):
            SocialScrapeProvider().validate_credentials({"api_key": "ss_live_not_a_real_key"})


if __name__ == "__main__":
    unittest.main()
