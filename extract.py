"""Step 1: turn application PDFs into a list of pages: {file, page, text}.

Every later step cites (file, page), so we keep both on every chunk of text.
"""
import re
import sys
from pathlib import Path

from pypdf import PdfReader


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def load_pdf(path: Path) -> list[dict]:
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = clean(page.extract_text())
        except Exception:  # a broken page shouldn't stop the whole package
            text = ""
        pages.append({
            "file": path.name,
            "page": i,
            "text": text,
            # Scanned pages have almost no text layer: flag them so the UI can say so.
            "scanned": len(text.split()) < 15,
        })
    return pages


def load_package(paths: list[Path]) -> list[dict]:
    pages = []
    for p in sorted(paths):
        pages.extend(load_pdf(p))
    return pages


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    pages = load_package(list(folder.glob("*.pdf")))
    by_file: dict[str, list[dict]] = {}
    for p in pages:
        by_file.setdefault(p["file"], []).append(p)
    for name, ps in by_file.items():
        scanned = sum(p["scanned"] for p in ps)
        print(f"{len(ps):4d} pages ({scanned} scanned)  {name}")
    print(f"total {len(pages)} pages")
