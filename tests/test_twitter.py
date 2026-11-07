import httpx
import pytest

from scdlbot import twitter
from scdlbot.ytdlp import DownloadError

PHOTO_TWEET = {
    "__typename": "Tweet",
    "text": "damon was so happy to be there #autismjoy via https://t.co/abc https://t.co/TGUH621ENF",
    "display_text_range": [0, 62],
    "entities": {"urls": [{"url": "https://t.co/abc", "expanded_url": "https://example.com/story"}], "media": [{"url": "https://t.co/TGUH621ENF"}]},
    "user": {"name": "tay", "screen_name": "hairball1952"},
    "mediaDetails": [
        {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"},
        {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.png"},
    ],
}


def client_for(answers: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pbs.twimg.com":
            return httpx.Response(200, content=b"\xff\xd8 photo " + request.url.path.encode())
        answer = answers.get(request.url.params["id"])
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(404 if answer is None else 200, json=answer or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_photo_tweet(tmp_path):
    async with client_for({"111": PHOTO_TWEET}) as client:
        tweet = await twitter.fetch("https://x.com/hairball1952/status/111?s=20", client)
        assert tweet is not None and not tweet.has_video
        assert tweet.text == "damon was so happy to be there #autismjoy via https://example.com/story"  # media link dropped, others expanded
        assert (tweet.author_name, tweet.handle) == ("tay", "hairball1952")
        items = await twitter.photo_items(tweet, tmp_path, client)
    assert [(i.path.name, i.kind, i.playlist_index) for i in items] == [("01.jpg", "photo", 1), ("02.png", "photo", 2)]
    assert items[0].author_url == "https://twitter.com/hairball1952" and items[0].path.read_bytes().startswith(b"\xff\xd8")


async def test_deleted_video_and_unreachable_tweets():
    tombstone = {"__typename": "TweetTombstone", "tombstone": {"text": {"text": "This Post was deleted by the Post author."}}}
    video = PHOTO_TWEET | {"mediaDetails": [{"type": "video"}]}
    async with client_for({"1": tombstone, "2": video, "3": httpx.ConnectError("down")}) as client:
        with pytest.raises(DownloadError) as error:
            await twitter.fetch("https://twitter.com/a/status/1", client)
        assert error.value.code == "unavailable"
        assert (await twitter.fetch("https://twitter.com/a/status/2", client)).has_video  # yt-dlp does videos
        assert await twitter.fetch("https://twitter.com/a/status/3", client) is None  # let yt-dlp try


def test_token_looks_like_the_embed_widget_one():
    token = twitter._token("2112697830687047918")
    assert token.isalnum() and "0" not in token and len(token) > 5


def test_for_ytdlp_drops_photo_index_but_keeps_video_index():
    assert twitter.for_ytdlp("https://x.com/estrella4ha/status/2112439359437746506/photo/1") == "https://x.com/estrella4ha/status/2112439359437746506"
    assert twitter.for_ytdlp("https://x.com/a/status/1/photo/2?s=20") == "https://x.com/a/status/1?s=20"
    assert twitter.for_ytdlp("https://x.com/a/status/1/video/2") == "https://x.com/a/status/1/video/2"
