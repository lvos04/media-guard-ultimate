"""Verzenders per kanaal. Elk kanaal krijgt dezelfde boodschap (titel, tekst, link,
prioriteit, optionele bijlage) en geeft bij een fout een ``ChannelError`` met een korte
Nederlandse reden (zonder geheimen)."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from mediaguard.secrets_store import redact

if TYPE_CHECKING:
    from mediaguard.context import AppContext

KINDS = ("ntfy", "pushover", "telegram", "discord", "slack", "webhook", "email")
KIND_LABELS_NL = {"ntfy": "ntfy (app op je telefoon)", "pushover": "Pushover",
                  "telegram": "Telegram", "discord": "Discord", "slack": "Slack",
                  "webhook": "Webhook", "email": "E-mail"}
# Velden per kanaal: (naam, geheim?, verplicht?)
FIELDS: dict[str, list[tuple[str, bool, bool]]] = {
    "ntfy": [("server", False, True), ("topic", False, True), ("token", True, False)],
    "pushover": [("user_key", True, True), ("app_token", True, True)],
    "telegram": [("bot_token", True, True), ("chat_id", False, True)],
    "discord": [("webhook_url", True, True)],
    "slack": [("webhook_url", True, True)],
    "webhook": [("url", True, True)],
    "email": [("host", False, True), ("port", False, True), ("security", False, True),
              ("username", False, False), ("password", True, False),
              ("from_addr", False, True), ("to_addr", False, True)],
}


class ChannelError(Exception):
    """Verzenden mislukt; ``retry`` = later opnieuw proberen zinvol."""

    def __init__(self, message: str, *, retry: bool = True) -> None:
        super().__init__(redact(message))
        self.retry = retry


@dataclass
class Message:
    title: str
    body: str = ""
    link: str | None = None
    priority: str = "default"  # low | default | high
    attachment: Path | None = None


def secret_name(channel_id: int, field: str) -> str:
    return f"notify.channel.{channel_id}.{field}"


def resolve(ctx: AppContext, channel: dict[str, Any]) -> dict[str, Any]:
    """Configuratie + geheimen van één kanaal (alleen in het geheugen)."""
    out = dict(channel.get("config") or {})
    for name, secret, _req in FIELDS[channel["kind"]]:
        if secret:
            out[name] = ctx.secrets.get(secret_name(int(channel["id"]), name)) or ""
    return out


def _client(ctx: AppContext) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8),
                             transport=ctx.services.get("notify_transport"))


async def _post(ctx: AppContext, url: str, **kwargs: Any) -> httpx.Response:
    try:
        async with _client(ctx) as client:
            response = await client.post(url, **kwargs)
    except httpx.HTTPError as exc:
        raise ChannelError(f"niet bereikbaar ({type(exc).__name__})") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise ChannelError(f"dienst antwoordde {response.status_code}")
    if response.status_code >= 400:
        raise ChannelError(f"geweigerd ({response.status_code}): {response.text[:160]}",
                           retry=False)
    return response


def _text(msg: Message, link_url: str | None) -> str:
    parts = [msg.body.strip()] if msg.body.strip() else []
    if link_url:
        parts.append(link_url)
    return "\n".join(parts)


async def send(ctx: AppContext, channel: dict[str, Any], msg: Message,
               link_url: str | None = None) -> None:
    kind = channel["kind"]
    cfg = resolve(ctx, channel)
    missing = [n for n, _s, req in FIELDS[kind] if req and not str(cfg.get(n) or "").strip()]
    if missing:
        raise ChannelError(f"niet volledig ingesteld ({', '.join(missing)})", retry=False)
    text = _text(msg, link_url)
    if kind == "ntfy":
        headers = {"Title": msg.title.encode("utf-8").decode("latin-1", "replace"),
                   "Priority": {"low": "2", "high": "4"}.get(msg.priority, "3"),
                   "Tags": "film_frames"}
        if link_url:
            headers["Click"] = link_url
        if cfg.get("token"):
            headers["Authorization"] = f"Bearer {cfg['token']}"
        url = str(cfg["server"]).rstrip("/") + "/" + str(cfg["topic"]).strip("/")
        if msg.attachment is not None:
            headers["Filename"] = msg.attachment.name
            headers["Message"] = (text or msg.title)[:2000].encode("utf-8").decode(
                "latin-1", "replace")
            await _post(ctx, url, content=msg.attachment.read_bytes(), headers=headers)
        else:
            await _post(ctx, url, content=(text or msg.title).encode("utf-8"), headers=headers)
    elif kind == "pushover":
        data = {"token": cfg["app_token"], "user": cfg["user_key"], "title": msg.title,
                "message": text or msg.title,
                "priority": {"low": "-1", "high": "1"}.get(msg.priority, "0")}
        if link_url:
            data["url"] = link_url
        await _post(ctx, "https://api.pushover.net/1/messages.json", data=data)
    elif kind == "telegram":
        base = f"https://api.telegram.org/bot{cfg['bot_token']}"
        body = f"<b>{_esc(msg.title)}</b>" + (f"\n{_esc(text)}" if text else "")
        await _post(ctx, f"{base}/sendMessage",
                    json={"chat_id": cfg["chat_id"], "text": body[:4000], "parse_mode": "HTML",
                          "disable_web_page_preview": True})
    elif kind == "discord":
        await _post(ctx, cfg["webhook_url"],
                    json={"username": "MediaGuard",
                          "embeds": [{"title": msg.title[:250], "description": text[:3900],
                                      **({"url": link_url} if link_url else {})}]})
    elif kind == "slack":
        await _post(ctx, cfg["webhook_url"],
                    json={"text": f"*{msg.title}*" + (f"\n{text}" if text else "")})
    elif kind == "webhook":
        await _post(ctx, cfg["url"], json={"source": "mediaguard", "title": msg.title,
                                           "body": msg.body, "link": link_url,
                                           "priority": msg.priority})
    elif kind == "email":
        await asyncio.to_thread(_send_email, ctx, cfg, msg, text)
    else:  # pragma: no cover - door FIELDS afgedekt
        raise ChannelError(f"onbekend kanaal {kind}", retry=False)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


SmtpFactory = Callable[[str, int, str], Any]


def _default_smtp(host: str, port: int, security: str) -> Any:
    if security == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
    client = smtplib.SMTP(host, port, timeout=20)
    if security == "starttls":
        client.starttls(context=ssl.create_default_context())
    return client


def _send_email(ctx: AppContext, cfg: dict[str, Any], msg: Message, text: str) -> None:
    factory: SmtpFactory = ctx.services.get("notify_smtp_factory") or _default_smtp
    mail = EmailMessage()
    mail["Subject"] = f"MediaGuard: {msg.title}"
    mail["From"] = str(cfg["from_addr"])
    mail["To"] = str(cfg["to_addr"])
    mail.set_content(text or msg.title)
    if msg.attachment is not None:
        mail.add_attachment(msg.attachment.read_bytes(), maintype="application",
                            subtype="octet-stream", filename=msg.attachment.name)
    try:
        port = int(cfg.get("port") or 587)
        client = factory(str(cfg["host"]), port, str(cfg.get("security") or "starttls"))
        try:
            if cfg.get("username"):
                client.login(str(cfg["username"]), str(cfg.get("password") or ""))
            client.send_message(mail)
        finally:
            client.quit()
    except smtplib.SMTPAuthenticationError as exc:
        raise ChannelError("inloggen bij de mailserver mislukt", retry=False) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise ChannelError(f"mailserver: {type(exc).__name__}") from exc
