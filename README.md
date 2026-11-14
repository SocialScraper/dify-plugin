# SocialScrape

Social media data inside your Dify agents and workflows, in one response shape for every platform.

**Author:** socialscrape · **Version:** 0.1.0 · **Type:** tool

Source repository: https://github.com/SocialScraper/dify-plugin

## What it does

| Tool | What it returns | Cost |
| --- | --- | --- |
| **Get a transcript** | The transcript of one video from its link, with the platform, language, duration and source. Works with YouTube, TikTok, Instagram, X, Facebook, LinkedIn, Reddit, Twitch clips, Kick clips and Rumble. Short videos without captions are transcribed from their audio. | 1 credit |
| **Get a profile** | A profile by handle on Instagram, TikTok, X, Threads, Bluesky, Snapchat, Twitch or Truth Social: name, biography, links, verification and follower counts. | 1 credit |
| **List a profile's posts** | The newest posts or videos of a profile on Instagram, TikTok, Threads, Bluesky or Truth Social, with a cursor for the next page. | 1 credit |
| **Get credits** | The credits left on your team. | Free |

Failed requests are free, and so is a repeated request answered from the cache.

## Setup

1. Create a SocialScrape account at [socialscrape.dev/sign-up](https://socialscrape.dev/sign-up). A new account starts with 100 free credits, with no card needed.
2. Create an API key in the dashboard under **API keys**.
3. In Dify, install the SocialScrape plugin from the Marketplace, open **Tools > SocialScrape > Authorize**, and paste the key. The key is checked against the API before it is saved.

## Usage

- **In an agent:** add the SocialScrape tools and ask, for example, "What does this TikTok say?" followed by the video link, or "How many followers does @nasa have on Instagram, and what did they post last?" The agent can look up the profile, list its posts and fetch transcripts on its own.
- **In a workflow:** add the **Get a transcript** node and pass a video link. The node's `text` output is the transcript, ready for the next node (an LLM, a knowledge base, a code node), and `json` holds the full answer.

A video longer than 2 minutes that has no captions is not transcribed unless you turn on **Transcribe long audio**, which costs 5 credits per started minute of the whole video. With it off, such a video answers with an error that names the cost and uses no credits. A long transcription can take longer than a tool call may wait; the tool then returns `status: "processing"` with a `job_id`. Run it again with the same link a minute or two later: the finished transcript is cached, so it uses credits only once.

## Requirements

- A SocialScrape API key (free to create).
- Outbound HTTPS access from your Dify instance to `api.socialscrape.dev` (port 443). Self-hosted Dify behind a firewall must allow it.

## Privacy

See [PRIVACY.md](PRIVACY.md). The plugin sends the video links and account handles you give it to the SocialScrape API, and nothing else.

## Support

hello@socialscrape.dev · API docs: [socialscrape.dev/docs](https://socialscrape.dev/docs)

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests          # mocked HTTP
SOCIALSCRAPE_LIVE_KEY=ss_live_... .venv/bin/python -m unittest discover -s tests   # plus live API
dify plugin package . -o socialscrape.difypkg
```
