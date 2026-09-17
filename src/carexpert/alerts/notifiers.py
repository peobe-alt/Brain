"""Where an alert goes once we decide it is worth your evening."""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str

    def send(self, subject: str, body: str, payload: dict) -> bool: ...


class ConsoleNotifier:
    name = "console"

    def send(self, subject: str, body: str, payload: dict) -> bool:
        print(f"\n=== {subject} ===\n{body}\n")
        return True


class WebhookNotifier:
    """Generic POST: Slack, Discord, Make, n8n, your own backend."""

    name = "webhook"

    def __init__(self, url: str | None = None) -> None:
        self.url = url or get_settings().webhook_url

    def send(self, subject: str, body: str, payload: dict) -> bool:
        if not self.url:
            log.warning("webhook non configure (CAREXPERT_WEBHOOK_URL)")
            return False
        try:
            response = httpx.post(
                self.url,
                json={"text": f"*{subject}*\n{body}", "subject": subject, "listing": payload},
                timeout=15.0,
            )
            return response.status_code < 300
        except httpx.HTTPError as exc:
            log.error("echec webhook: %s", exc)
            return False


class TelegramNotifier:
    name = "telegram"

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        settings = get_settings()
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id

    def send(self, subject: str, body: str, payload: dict) -> bool:
        if not self.token or not self.chat_id:
            log.warning("telegram non configure (token / chat id)")
            return False
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": f"*{subject}*\n{body}",
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                },
                timeout=15.0,
            )
            return response.status_code < 300
        except httpx.HTTPError as exc:
            log.error("echec telegram: %s", exc)
            return False


class EmailNotifier:
    name = "email"

    def send(self, subject: str, body: str, payload: dict) -> bool:
        settings = get_settings()
        if not (settings.smtp_host and settings.alert_email_to):
            log.warning("email non configure (smtp host / destinataire)")
            return False
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.smtp_user or "carexpert@localhost"
        message["To"] = settings.alert_email_to
        message.set_content(body)
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
                server.starttls()
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.error("echec email: %s", exc)
            return False


REGISTRY: dict[str, type] = {
    "console": ConsoleNotifier,
    "webhook": WebhookNotifier,
    "telegram": TelegramNotifier,
    "email": EmailNotifier,
}


def get_notifiers(names: list[str]) -> list[Notifier]:
    notifiers: list[Notifier] = []
    for name in names or ["console"]:
        factory = REGISTRY.get(name)
        if factory is None:
            log.warning("canal de notification inconnu: %s", name)
            continue
        notifiers.append(factory())
    return notifiers


def format_alert(listing, score, valuation, report) -> tuple[str, str]:
    """A message you can act on from a phone, in ten seconds."""
    title = f"{score.score}/100 - {listing.title}"
    price = f"{(listing.price_eur or 0):,.0f}".replace(",", " ")
    fair = f"{(valuation.fair_price_eur if valuation else 0):,.0f}".replace(",", " ")
    lines = [
        score.headline,
        "",
        f"Prix demande: {price} EUR   |   Estimation marche: {fair} EUR",
        f"{listing.year or '?'} - {(listing.km or 0):,} km".replace(",", " ")
        + f" - {listing.fuel.value} - {listing.gearbox.value}",
        f"Vendeur: {listing.seller_type.value} - {listing.city or 'lieu inconnu'} ({listing.country})",
        "",
    ]
    if report is not None:
        lines.append(report.summary)
        if report.red_flags:
            lines.append("")
            lines.append("Points d'attention:")
            lines += [f"  - {flag.label} ({flag.severity})" for flag in report.red_flags[:4]]
        if report.questions_to_seller:
            lines.append("")
            lines.append("A demander au vendeur:")
            lines += [f"  - {question}" for question in report.questions_to_seller[:3]]
    lines.append("")
    lines.append(listing.url)
    return title, "\n".join(lines)


def json_payload(listing, score, valuation, report) -> dict:
    return {
        "url": listing.url,
        "title": listing.title,
        "price_eur": listing.price_eur,
        "score": score.score,
        "verdict": score.verdict,
        "net_gain_eur": score.net_gain_eur,
        "valuation": valuation.as_dict() if valuation else None,
        "report": json.loads(report.model_dump_json()) if report else None,
    }
