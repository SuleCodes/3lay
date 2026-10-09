"""Reading attachments from a raw email, and turning documents into image blocks."""

from conftest import email_message, pdf_bytes, png_bytes, write_email
from obed.documents import page_count, to_image_blocks
from orchy.emails import MAX_DEPTH, read_attachment, read_attachments


def test_reads_attachments_in_order_with_their_types(tmp_path):
    path = write_email(tmp_path / "e.eml",
                       ("jan.pdf", "application/pdf", pdf_bytes()),
                       ("photo.png", "image/png", png_bytes()))

    attachments = read_attachments(path)

    assert [(a.index, a.name, a.content_type) for a in attachments] == [
        (0, "jan.pdf", "application/pdf"), (1, "photo.png", "image/png"),
    ]
    assert attachments[0].data.startswith(b"%PDF")


def test_the_email_body_is_not_an_attachment(tmp_path):
    assert read_attachments(write_email(tmp_path / "e.eml")) == []


def test_octet_stream_uses_the_file_extension(tmp_path):
    path = write_email(tmp_path / "e.eml",
                       ("statement.PDF", "application/octet-stream", pdf_bytes()))

    assert read_attachments(path)[0].content_type == "application/pdf"


def test_read_attachment_gets_one_by_index(tmp_path):
    path = write_email(tmp_path / "e.eml",
                       ("a.pdf", "application/pdf", pdf_bytes(1)),
                       ("b.pdf", "application/pdf", pdf_bytes(3)))

    assert read_attachment(path, 1).name == "b.pdf"


def test_pdfs_render_one_block_per_page_and_photos_go_as_they_are():
    assert page_count(pdf_bytes(3), "application/pdf") == 3
    assert len(to_image_blocks(pdf_bytes(3), "application/pdf", dpi=20)) == 3

    [block] = to_image_blocks(png_bytes(), "image/png")
    assert block["type"] == "image" and block["mime_type"] == "image/png"


# Forwarded emails

def statement_email(sender, *names):
    """An agency's email with PDFs, as the actor received it."""
    return email_message(*[(n, "application/pdf", pdf_bytes()) for n in names], sender=sender)


def test_a_normal_forward_is_just_attachments(tmp_path):
    """Gmail/Outlook "Forward" copies the attachments into the new email."""
    path = write_email(tmp_path / "e.eml", ("statement.pdf", "application/pdf", pdf_bytes()),
                       body="---------- Forwarded message ---------")

    [attachment] = read_attachments(path)

    assert (attachment.name, attachment.location, attachment.forwarded_from) == (
        "statement.pdf", "0", None)


def test_forward_as_attachment_finds_the_documents_inside(tmp_path):
    forwarded = statement_email("agency@example.com", "jan.pdf", "feb.pdf")
    path = tmp_path / "e.eml"
    path.write_bytes(email_message(forwarded).as_bytes())

    attachments = read_attachments(path)

    assert [(a.index, a.name, a.location, a.forwarded_from) for a in attachments] == [
        (0, "jan.pdf", "0 > 0", "agency@example.com"),
        (1, "feb.pdf", "0 > 1", "agency@example.com"),
    ]
    assert attachments[1].data.startswith(b"%PDF")
    assert read_attachment(path, 1).name == "feb.pdf"  # found again by index


def test_direct_and_forwarded_attachments_together_keep_their_order(tmp_path):
    forwarded = statement_email("agency@example.com", "inside.pdf")
    path = tmp_path / "e.eml"
    path.write_bytes(email_message(("before.pdf", "application/pdf", pdf_bytes()),
                                   forwarded,
                                   ("after.png", "image/png", png_bytes())).as_bytes())

    assert [(a.name, a.location) for a in read_attachments(path)] == [
        ("before.pdf", "0"), ("inside.pdf", "1 > 0"), ("after.png", "2")]


def test_a_forward_of_a_forward_is_opened_and_names_the_original_sender(tmp_path):
    original = statement_email("agency@example.com", "statement.pdf")
    middle = email_message(original, sender="manager@example.com")
    path = tmp_path / "e.eml"
    path.write_bytes(email_message(middle).as_bytes())

    [attachment] = read_attachments(path)

    assert attachment.location == "0 > 0 > 0"
    assert attachment.forwarded_from == "agency@example.com"


def test_forwarding_deeper_than_the_limit_stops_opening_emails(tmp_path):
    message = statement_email("agency@example.com", "statement.pdf")
    for _ in range(MAX_DEPTH + 1):
        message = email_message(message)
    path = tmp_path / "e.eml"
    path.write_bytes(message.as_bytes())

    [attachment] = read_attachments(path)

    assert attachment.content_type == "message/rfc822"  # left closed: unsupported
