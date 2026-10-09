"""Reading attachments from a raw email, and turning documents into image blocks."""

from conftest import pdf_bytes, png_bytes, write_email
from obed.documents import page_count, to_image_blocks
from orchy.emails import read_attachment, read_attachments


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
