import io


def extract_text(filename: str, content: bytes) -> str | None:
    """
    Extract plain text from document content.

    Returns a string (truncated to 1500 words) for supported formats,
    or None for formats where text extraction is not possible (e.g. DrawIO,
    Visio, images). Callers should fall back to filename/path inference only
    when None is returned.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in ("md", "txt"):
        text = content.decode("utf-8", errors="replace")

    elif ext == "docx":
        try:
            from docx import Document
            doc = Document(io.BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as exc:
            print(f"  WARNING: could not extract text from {filename}: {exc}")
            return None

    elif ext == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages_text = []
            for page in reader.pages:
                pages_text.append(page.extract_text() or "")
            text = "\n".join(pages_text)
        except Exception as exc:
            print(f"  WARNING: could not extract text from {filename}: {exc}")
            return None

    else:
        # Binary formats (drawio, vsdx, png, jpg, xlsx, pptx, etc.)
        return None

    words = text.split()
    if not words:
        return None
    return " ".join(words[:1500])
