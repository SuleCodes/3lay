"""Wraps PDFs or photos in a raw email (.eml), like the ones the ingest Function stores.

    python scripts/make_email.py tmp/fixtures/statement.pdf
    python scripts/make_email.py jan.pdf feb.jpg --out tmp/fixtures/two-statements.eml

The email is written next to the first file (same name, .eml) unless --out is
given. Use invented documents for anything that leaves this machine (e.g. a
traced or delivered run).
"""

import argparse
import mimetypes
import sys
from email.message import EmailMessage
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="PDFs or photos to attach")
    parser.add_argument("--out", type=Path, help="where to write the .eml")
    parser.add_argument("--sender", default="actor@example.com")
    parser.add_argument("--to", default="rolepay-agent@in.3lay.live")
    parser.add_argument("--subject", default="My statement")
    args = parser.parse_args()

    message = EmailMessage()
    message["From"] = args.sender
    message["To"] = args.to
    message["Subject"] = args.subject
    message.set_content("Please find my statement attached.")
    for file in args.files:
        if not file.is_file():
            sys.exit(f"Not a file: {file}")
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        maintype, subtype = content_type.split("/")
        message.add_attachment(file.read_bytes(), maintype=maintype, subtype=subtype,
                               filename=file.name)

    out = args.out or args.files[0].with_suffix(".eml")
    out.write_bytes(message.as_bytes())
    print(f"Wrote {out} with {len(args.files)} attachment(s)")


if __name__ == "__main__":
    main()
