"""Getting advert photos into the model's context, cheaply.

Photos are where the truth is: panel gaps, mismatched paint, tyre wear, a
worn interior on a car claiming 80 000 km. But they are also the expensive
part of the request, so they are capped, downscaled and deduplicated first.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from io import BytesIO

import httpx

from ..config import get_settings
from ..schemas import Photo

log = logging.getLogger(__name__)

MAX_BYTES = 4_500_000          # per-image API ceiling, with margin

#: Magic bytes, because `Content-Type` lies. Classified sites routinely
#: answer 200 with an HTML error page for a missing photo, and sending that
#: to the API as base64 `image/jpeg` fails the whole analysis of the advert.
MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_media_type(content: bytes) -> str | None:
    """The real type of these bytes, or None if they are not an image."""
    for prefix, media_type in MAGIC:
        if content.startswith(prefix):
            return media_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(slots=True)
class PreparedPhoto:
    index: int
    url: str
    media_type: str
    data_b64: str

    def as_block(self) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": self.media_type,
                "data": self.data_b64,
            },
        }


def prepare_photos(
    photos: list[Photo], *, limit: int | None = None, max_edge: int | None = None
) -> list[PreparedPhoto]:
    """Download, downscale and encode the first `limit` usable photos.

    Never raises: a photo that cannot be fetched is simply left out, so an
    offline run still produces a (text-only) analysis.
    """
    settings = get_settings()
    limit = limit or settings.max_photos
    max_edge = max_edge or settings.photo_max_edge

    prepared: list[PreparedPhoto] = []
    seen: set[str] = set()
    with httpx.Client(timeout=15.0, follow_redirects=True,
                      headers={"User-Agent": settings.user_agent}) as client:
        for index, photo in enumerate(photos):
            if len(prepared) >= limit:
                break
            if photo.url in seen:
                continue
            seen.add(photo.url)
            try:
                response = client.get(photo.url)
                if response.status_code != 200 or not response.content:
                    continue
                content = response.content
                media_type = sniff_media_type(content)
                if media_type is None:
                    # Not an image at all: an HTML error page, a placeholder,
                    # or a format the API does not accept. Skip it silently.
                    log.debug("photo ignoree (contenu non image) %s", photo.url)
                    continue
                content, media_type = _downscale(content, media_type, max_edge)
                if len(content) > MAX_BYTES:
                    continue
                prepared.append(
                    PreparedPhoto(
                        index=index,
                        url=photo.url,
                        media_type=media_type,
                        data_b64=base64.standard_b64encode(content).decode("ascii"),
                    )
                )
            except (httpx.HTTPError, ValueError) as exc:
                log.debug("photo ignoree %s: %s", photo.url, exc)
    return prepared


def _downscale(content: bytes, media_type: str, max_edge: int) -> tuple[bytes, str]:
    """Resize with Pillow when available; pass through otherwise."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - optional dependency
        return content, media_type
    try:
        image = Image.open(BytesIO(content))
        image.load()
        if max(image.size) <= max_edge and len(content) < 1_500_000:
            return content, media_type
        image.thumbnail((max_edge, max_edge))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=True)
        return buffer.getvalue(), "image/jpeg"
    except Exception as exc:  # pragma: no cover - corrupt image
        log.debug("redimensionnement impossible: %s", exc)
        return content, media_type
