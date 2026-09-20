"""The endpoints the browser extension talks to.

The extension sends the page the user is reading; the server answers with a
verdict per advert. Nothing is fetched from the site: the browser already
has the page.

One thing deserves care here. A server listening on 127.0.0.1 is reachable
by *any* page open in the browser, not only by the ones we expect: a site
can post to localhost from its own JavaScript, and the request goes through
even when the answer is unreadable to it. So requests carrying an `Origin`
are only accepted from the sites CarExpert knows, from a browser extension,
or from the dashboard itself.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import __version__
from ..config import get_settings
from ..db import Listing, session_scope
from ..pipeline.capture import absorb, payload
from ..pipeline.run import deep_analyze
from ..sources.capture import TLD_COUNTRIES, read_page
from ..sources.configured import load_site_configs

log = logging.getLogger(__name__)

EXTENSION_DIR = Path(__file__).resolve().parent.parent / "extension"
GUARDED_PREFIX = "/api/extension"

#: Une page de resultats elaguee pese quelques centaines de kilo-octets. Au
#: dela, ce n'est plus une page d'annonces, et la refuser vaut mieux que
#: passer une minute a la parser.
MAX_HTML_BYTES = 8_000_000

EXTENSION_SCHEMES = ("chrome-extension", "moz-extension", "safari-web-extension",
                     "extension", "ms-browser-extension")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")
#: Suffixes de second niveau: sans eux `autoscout24.co.uk` se lirait "co".
SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac"}
#: Pays d'un site -> extension de domaine, pour reconnaitre le meme site
#: dans chacun de ses pays sans avoir a tous les ecrire.
COUNTRY_TLDS = {country: tld for tld, country in TLD_COUNTRIES.items()}

router = APIRouter()


@dataclass
class Activity:
    """What the extension has sent since the server started.

    Installing an extension is the one step of this tool where nothing on
    screen says whether it worked. This is what the page reads to say it.
    """

    pages: int = 0
    listings: int = 0
    last_at: datetime | None = None
    last_url: str = ""
    #: Poids de la derniere page recue. C'est la seule chose qui sort du
    #: navigateur, et l'utilisateur a le droit de savoir combien.
    last_bytes: int = 0

    def note(self, url: str, count: int, page_bytes: int = 0) -> None:
        self.pages += 1
        self.listings += count
        self.last_at = datetime.utcnow()
        self.last_url = url[:300]
        self.last_bytes = page_bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "listings": self.listings,
            "last_url": self.last_url,
            "last_at": self.last_at.isoformat() if self.last_at else None,
            "last_bytes": self.last_bytes,
            "last_size": f"{self.last_bytes / 1024:.0f} Ko" if self.last_bytes else "",
        }


activity = Activity()


# --- Qui a le droit de parler au serveur ----------------------------------


def split_host(host: str) -> tuple[str, str]:
    """`www.autoscout24.co.uk` -> ("autoscout24", "co.uk")."""
    labels = [label for label in host.lower().split(":")[0].split(".") if label]
    if len(labels) < 2:
        return "", ""
    if labels[-2] in SECOND_LEVEL and len(labels) >= 3:
        return labels[-3], ".".join(labels[-2:])
    return labels[-2], labels[-1]


def known_site_hosts() -> set[str]:
    """Every domain the extension may legitimately speak for.

    Built from the site files, so adding a country to a YAML is enough: a
    German user browsing autoscout24.de must not be refused because the
    template happens to name the French domain.
    """
    hosts: set[str] = set()
    for config in load_site_configs().values():
        name, tld = split_host(urlparse(config.get("base_url") or "").netloc)
        if not name:
            continue
        tlds = {tld}
        for country in config.get("countries") or []:
            country_tld = COUNTRY_TLDS.get(str(country).upper())
            if country_tld:
                tlds.add(country_tld)
        if "uk" in tlds:
            tlds.add("co.uk")
        hosts |= {f"{name}.{suffix}" for suffix in tlds}
    return hosts


def origin_allowed(origin: str) -> bool:
    """Whether a browser origin may post pages to this server.

    An absent `Origin` is not a cross-site call: the dashboard's own GETs and
    the command line have none. A present one must be a site we know, a
    browser extension, or the dashboard itself.
    """
    if not origin or origin == "null":
        return True
    parsed = urlparse(origin)
    if parsed.scheme in EXTENSION_SCHEMES:
        return True
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").split(":")[0].lower()
    if host in LOCAL_HOSTS:
        return True
    # Comparaison sur le domaine complet, jamais sur "contient": sinon
    # `autoscout24.pirate.example` passerait pour AutoScout24, et une page
    # hostile remplirait la base de faux comparables.
    name, tld = split_host(host)
    return f"{name}.{tld}" in known_site_hosts()


def _cors_headers(origin: str) -> dict[str, str]:
    if not origin:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


async def guard_origin(request: Request, call_next):
    """Refuse unknown origins, and answer the browser's preflight."""
    if not request.url.path.startswith(GUARDED_PREFIX):
        return await call_next(request)

    origin = request.headers.get("origin", "")
    if not origin_allowed(origin):
        log.warning("extension: origine refusee %s", origin[:120])
        return JSONResponse(
            {"ok": False, "message": "Origine non autorisee."},
            status_code=403,
        )
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_cors_headers(origin))

    response = await call_next(request)
    for key, value in _cors_headers(origin).items():
        response.headers[key] = value
    return response


# --- Ce que l'extension envoie --------------------------------------------


class PageCapture(BaseModel):
    url: str
    html: str
    title: str | None = None


class DeepRequest(BaseModel):
    id: int | None = None
    url: str | None = Field(default=None)


@router.get("/api/extension/status")
def status() -> dict[str, Any]:
    """Told the extension it is talking to a live CarExpert."""
    settings = get_settings()
    with session_scope() as session:
        listings = session.execute(select(func.count(Listing.id))).scalar_one()
    return {
        "ok": True,
        "version": __version__,
        "listings": listings,
        # Sans cle API le bouton d'expertise approfondie n'a rien a appeler:
        # mieux vaut ne pas le proposer que le faire echouer.
        "deep": bool(settings.anthropic_api_key),
        "sites": sorted(name for name in load_site_configs()),
        "activity": activity.as_dict(),
    }


@router.post("/api/extension/page")
def analyse_page(capture: PageCapture) -> dict[str, Any]:
    """Read one captured page, value its adverts, answer with the verdicts."""
    page_bytes = len(capture.html.encode("utf-8", "ignore"))
    if page_bytes > MAX_HTML_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Page trop lourde pour etre analysee.",
        )

    page = read_page(capture.html, capture.url)
    try:
        with session_scope() as session:
            outcome = absorb(session, page)
    except Exception as exc:  # une panne ici s'affiche sur le site du vendeur
        # L'utilisateur est en train de lire une annonce, pas un journal
        # d'erreurs: "CarExpert a repondu 500" ne lui apprend rien et ne lui
        # dit pas quoi faire. La trace complete reste cote serveur.
        log.exception("extension: lecture de page impossible")
        raise HTTPException(
            status_code=500,
            detail=(
                "CarExpert n'a pas pu enregistrer cette page "
                f"({type(exc).__name__}). Regardez la fenetre CarExpert, "
                "puis rechargez la page."
            ),
        ) from exc
    activity.note(capture.url, len(outcome.results), page_bytes)
    return outcome.as_dict()


@router.post("/api/extension/deep")
def analyse_deep(request: DeepRequest) -> dict[str, Any]:
    """Send one advert to Claude, on an explicit click, because it costs."""
    if not get_settings().anthropic_api_key:
        return {
            "ok": False,
            "message": "Aucune cle API Claude configuree: expertise sur les regles seules.",
        }

    try:
        return _deepen(request)
    except HTTPException:
        raise
    except Exception as exc:  # meme raison: le message s'affiche sur le site
        log.exception("extension: expertise approfondie impossible")
        return {
            "ok": False,
            "message": f"Expertise impossible ({type(exc).__name__}). "
                       "Regardez la fenetre CarExpert.",
        }


def _deepen(request: DeepRequest) -> dict[str, Any]:
    with session_scope() as session:
        row = _find(session, request)
        if row is None:
            raise HTTPException(status_code=404, detail="annonce inconnue de CarExpert")
        result, score = deep_analyze(session, row)
        if score is None:
            return {"ok": False, "message": f"Expertise indisponible: {result.degraded_reason}"}
        session.flush()
        return {
            "ok": True,
            "message": "Expertise approfondie terminee.",
            "cost_eur": result.cost().eur,
            "photos_analyzed": result.photos_analyzed,
            "result": payload(session, row),
        }


def _find(session, request: DeepRequest) -> Listing | None:
    if request.id:
        return session.get(Listing, request.id)
    if request.url:
        return session.execute(
            select(Listing).where(Listing.url == request.url)
        ).scalars().first()
    return None


# --- Installer l'extension ------------------------------------------------


def build_archive() -> bytes:
    """Zip the extension folder, so installing it needs no terminal."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EXTENSION_DIR.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                archive.write(path, path.relative_to(EXTENSION_DIR).as_posix())
    return buffer.getvalue()


@router.get("/extension/carexpert-extension.zip")
def download_archive() -> Response:
    return Response(
        content=build_archive(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="carexpert-extension.zip"'},
    )
