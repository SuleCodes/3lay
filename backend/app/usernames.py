"""Rules for client usernames.

A username is the local part of the client's forwarding address --
`rolepay-agent` becomes `rolepay-agent@<CLIENT_FORWARDING_DOMAIN>` -- so it
has to be a valid, unambiguous email local part. It also ends up as the
first folder of every blob path and in the X-3lay-Client header, which is
why the allowed characters are deliberately narrower than email allows.
"""

import re

MIN_LENGTH = 3
MAX_LENGTH = 32

# Lowercase letters, digits and hyphens; starts and ends with a letter or
# digit; no consecutive hyphens.
_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Addresses that mean something to mail systems or to people reading them,
# plus names that would impersonate 3lay itself.
RESERVED = frozenset(
    {
        "abuse", "admin", "administrator", "api", "billing", "bounce", "bounces",
        "contact", "dmarc", "help", "hostmaster", "info", "legal", "mail",
        "mailer-daemon", "no-reply", "noreply", "postmaster", "privacy", "root",
        "sales", "security", "signin", "support", "system", "team", "webmaster",
        "www", "3lay",
    }
)


def normalize_username(raw: str) -> str:
    """Returns the canonical (lowercase, trimmed) form of a username, or
    raises ValueError with a message that's safe to show the user."""
    username = raw.strip().lower()

    if not MIN_LENGTH <= len(username) <= MAX_LENGTH:
        raise ValueError(f"Username must be {MIN_LENGTH}-{MAX_LENGTH} characters long.")
    if not _PATTERN.match(username):
        raise ValueError(
            "Username can only contain lowercase letters, numbers and single hyphens, "
            "and must start and end with a letter or number."
        )
    if username in RESERVED:
        raise ValueError("That username is reserved. Please choose another.")
    return username
