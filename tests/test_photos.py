"""Photo preparation, against a real HTTP server and real images.

Photos are the expensive half of an expert analysis and the half that
carries the evidence. This exercises the actual download, resize and
encoding path rather than mocking it away.
"""

from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest

PIL = pytest.importorskip("PIL", reason="Pillow requis pour le redimensionnement")
from PIL import Image  # noqa: E402

from carexpert.expert.photos import prepare_photos  # noqa: E402
from carexpert.schemas import Photo  # noqa: E402


def _jpeg(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), (90, 110, 140))
    # Some texture, so the encoder cannot compress it to nothing.
    for x in range(0, width, 40):
        for y in range(0, height, 40):
            image.putpixel((x, y), (250, 40, 40))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


ROUTES: dict[str, tuple[str, bytes]] = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        route = ROUTES.get(self.path.split("?")[0])
        if route is None:
            self.send_error(404)
            return
        content_type, payload = route
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test output clean
        return


@pytest.fixture(scope="module")
def server():
    ROUTES.clear()
    ROUTES["/grande.jpg"] = ("image/jpeg", _jpeg(2400, 1800))
    ROUTES["/petite.jpg"] = ("image/jpeg", _jpeg(640, 480))
    ROUTES["/sans-type"] = ("application/octet-stream", _jpeg(800, 600))
    ROUTES["/pas-une-image.jpg"] = ("text/html", b"<html>404 deguise</html>")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _decode(prepared) -> Image.Image:
    return Image.open(BytesIO(base64.standard_b64decode(prepared.data_b64)))


def test_a_large_photo_is_downscaled(server):
    prepared = prepare_photos([Photo(url=f"{server}/grande.jpg")], max_edge=1024)
    assert len(prepared) == 1
    image = _decode(prepared[0])
    assert max(image.size) == 1024
    assert prepared[0].media_type == "image/jpeg"


def test_a_small_photo_is_left_alone(server):
    prepared = prepare_photos([Photo(url=f"{server}/petite.jpg")], max_edge=1024)
    assert _decode(prepared[0]).size == (640, 480)


def test_the_limit_is_respected(server):
    photos = [Photo(url=f"{server}/petite.jpg?{i}") for i in range(10)]
    assert len(prepare_photos(photos, limit=4)) == 4


def test_duplicate_urls_are_sent_once(server):
    photos = [Photo(url=f"{server}/petite.jpg")] * 5
    assert len(prepare_photos(photos, limit=8)) == 1


def test_indices_follow_the_advert_order(server):
    photos = [
        Photo(url=f"{server}/introuvable.jpg"),
        Photo(url=f"{server}/petite.jpg"),
        Photo(url=f"{server}/grande.jpg"),
    ]
    prepared = prepare_photos(photos, limit=8)
    # The missing photo is skipped, the others keep their original position so
    # "photo 2" in the report still means the third photo of the advert.
    assert [p.index for p in prepared] == [1, 2]


def test_unreachable_photos_never_break_the_analysis(server):
    photos = [Photo(url=f"{server}/introuvable.jpg"), Photo(url="http://127.0.0.1:1/x.jpg")]
    assert prepare_photos(photos, limit=8) == []


def test_a_missing_content_type_still_works(server):
    prepared = prepare_photos([Photo(url=f"{server}/sans-type")], max_edge=1024)
    assert len(prepared) == 1
    assert prepared[0].media_type in ("image/jpeg", "image/png")


def test_an_html_page_served_as_an_image_is_dropped(server):
    """Sites answer 200 with an error page more often than they should."""
    assert prepare_photos([Photo(url=f"{server}/pas-une-image.jpg")], limit=4) == []


def test_the_payload_is_a_valid_api_image_block(server):
    prepared = prepare_photos([Photo(url=f"{server}/petite.jpg")])
    block = prepared[0].as_block()
    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"].startswith("image/")
    base64.standard_b64decode(block["source"]["data"])  # must decode cleanly


@pytest.mark.parametrize(
    "content,expected",
    [
        (b"\xff\xd8\xff\xe0abc", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nabc", "image/png"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8", "image/webp"),
        (b"GIF89a...", "image/gif"),
        (b"<html><body>oups</body></html>", None),
        (b"", None),
    ],
)
def test_the_real_type_is_read_from_the_bytes(content, expected):
    from carexpert.expert.photos import sniff_media_type

    assert sniff_media_type(content) == expected
