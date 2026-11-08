import logging
import re
import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Mail:
    to: str
    subject: str
    text: str


class Mailer(Protocol):
    def send(self, mail: Mail) -> None: ...


class FileMailer:
    """Used when SMTP_HOST is empty: writes each email to tmp/mail/ instead of sending it."""

    def __init__(self, directory: Path = Path("tmp/mail")):
        self.directory = directory

    def send(self, mail: Mail) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        safe_to = re.sub(r"[^a-zA-Z0-9@._-]", "_", mail.to)
        path = self.directory / f"{int(time.time() * 1000)}-{safe_to}.txt"
        path.write_text(f"To: {mail.to}\nSubject: {mail.subject}\n\n{mail.text}\n", encoding="utf-8")
        logger.info("Mail written to %s", path)


class SmtpMailer:
    def send(self, mail: Mail) -> None:
        settings = get_settings()
        message = EmailMessage()
        message["From"] = settings.mail_from
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message.set_content(mail.text)

        smtp_class = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
        with smtp_class(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if smtp_class is smtplib.SMTP:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_pass)
            smtp.send_message(message)


def create_mailer() -> Mailer:
    return SmtpMailer() if get_settings().smtp_host else FileMailer()
