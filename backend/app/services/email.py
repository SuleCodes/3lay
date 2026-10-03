import logging

from app.config import get_settings

logger = logging.getLogger("3lay.email")
settings = get_settings()


class EmailDeliveryError(Exception):
    """The email provider refused or failed to send a message. The provider's
    reason is logged; the message here is safe to show to the caller."""


def send_magic_link_email(to_email: str, link: str) -> None:
    """Sends the sign-in link. Falls back to logging it to the console when
    no RESEND_API_KEY is configured, so the whole auth flow works locally
    with zero email setup -- just copy the link from the backend logs.

    Raises EmailDeliveryError if Resend rejects the send (e.g. the sender
    domain isn't verified, or the API key is invalid)."""
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set -- printing magic link instead of emailing it.")
        print(f"\n[3lay] Magic link for {to_email}:\n  {link}\n")
        return

    import resend
    from resend.exceptions import ResendError

    resend.api_key = settings.resend_api_key
    try:
        resend.Emails.send(
            {
                "from": settings.email_from,
                "to": [to_email],
                "subject": "Sign in to 3lay",
                "html": (
                    f"<p>Click below to sign in to 3lay. This link expires in "
                    f"{settings.magic_link_expire_minutes} minutes.</p>"
                    f'<p><a href="{link}">{link}</a></p>'
                    f"<p>If you didn't request this, you can ignore this email.</p>"
                ),
            }
        )
    except ResendError as exc:
        # Log Resend's own explanation (e.g. "You can only send testing emails
        # to your own email address..." while EMAIL_FROM is still the
        # onboarding@resend.dev sandbox sender). Never log the link itself:
        # anyone who can read the logs could use it to sign in.
        logger.error(
            "Resend refused the magic link email to %s from %s: %s",
            to_email,
            settings.email_from,
            getattr(exc, "message", exc),
        )
        raise EmailDeliveryError("We couldn't send the sign-in email. Please try again in a moment.") from exc
    except Exception as exc:  # network errors, timeouts
        logger.exception("Failed to reach Resend sending the magic link email to %s", to_email)
        raise EmailDeliveryError("We couldn't send the sign-in email. Please try again in a moment.") from exc
