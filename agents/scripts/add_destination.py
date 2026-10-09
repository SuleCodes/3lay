"""Adds a webhook destination for local runs, with a new signing secret.

    python scripts/add_destination.py https://webhook.site/your-unique-id
    python scripts/add_destination.py https://... --id dest_rolepay_main --events event.completed

Writes the destination to tmp/destinations.json (standing in for the client's
configuration) and its secret to agents/.env as NOTI_SECRET_<ID> (standing in
for the encrypted destination_secrets table). The secret is printed once, as a
client would see it, so you can verify deliveries with a Standard Webhooks
library. Replaces a destination with the same ID, keeping its secret unless
--new-secret is given (a rotation).

webhook.site is public: only run invented documents to it.
"""

import argparse
import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from noti.destinations import EVENT_TYPES, check_destinations, secret_variable  # noqa: E402
from noti.signing import generate_secret  # noqa: E402

DESTINATIONS_PATH = AGENTS_DIR / "tmp" / "destinations.json"
ENV_PATH = AGENTS_DIR / ".env"


def env_lines():
    return ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--id", default="dest_local_test")
    parser.add_argument("--events", nargs="+", default=EVENT_TYPES, choices=EVENT_TYPES)
    parser.add_argument("--new-secret", action="store_true",
                        help="replace the destination's secret (a rotation)")
    args = parser.parse_args()

    destinations = (json.loads(DESTINATIONS_PATH.read_text(encoding="utf-8"))
                    if DESTINATIONS_PATH.exists() else [])
    destinations = [d for d in destinations if d["id"] != args.id]
    destinations.append({"id": args.id, "type": "webhook", "url": args.url,
                         "events": args.events})
    check_destinations(destinations)

    variable = secret_variable(args.id)
    lines = env_lines()
    has_secret = any(line.startswith(f"{variable}=") for line in lines)
    secret = None
    if args.new_secret or not has_secret:
        secret = generate_secret()
        lines = [line for line in lines if not line.startswith(f"{variable}=")]
        lines.append(f"{variable}={secret}")
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    DESTINATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DESTINATIONS_PATH.write_text(json.dumps(destinations, indent=2) + "\n", encoding="utf-8")
    print(f"Destination {args.id} -> {args.url} ({', '.join(args.events)}) "
          f"saved to {DESTINATIONS_PATH}")
    if secret:
        print(f"Signing secret saved to {ENV_PATH} as {variable}:")
        print(f"  {secret}")
        print("This is what the client would be shown, to verify deliveries.")
    else:
        print(f"Kept its existing secret ({variable} in {ENV_PATH}).")


if __name__ == "__main__":
    main()
