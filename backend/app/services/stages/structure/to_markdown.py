from pathlib import Path
from docling.document_converter import DocumentConverter


def to_markdown(path):

    source = Path(path)

    converter = DocumentConverter()
    result = converter.convert(source)

    markdown_out = result.document.export_to_markdown()
    return markdown_out