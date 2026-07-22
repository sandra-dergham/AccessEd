from __future__ import annotations

from typing import Any


def compact_span(span: dict[str, Any]) -> dict[str, Any]:
    font = span.get("font") or {}
    layout = span.get("layout") or {}

    return {
        "id": span.get("id"),
        "page_index": span.get("page_index"),
        "text": span.get("text"),
        "bbox": span.get("bbox"),
        "font": {
            "name": font.get("name"),
            "size": font.get("size"),
            "flags": font.get("flags"),
        },
        "layout": {
            "block_index": layout.get("block_index"),
            "line_index": layout.get("line_index"),
        },
    }


def compact_image(image: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": image.get("id"),
        "page_index": image.get("page_index"),
        "bbox": image.get("bbox"),
        "source": image.get("source") or image.get("filename"),
        "alt_text": image.get("alt_text"),
    }


def compact_link(link: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": link.get("id"),
        "page_index": link.get("page_index"),
        "bbox": link.get("bbox"),
        "uri": link.get("uri"),
        "text": link.get("text"),
    }


def compact_layout_json(
    parsed_document: dict[str, Any],
) -> dict[str, Any]:
    spans = parsed_document.get("spans") or []
    images = parsed_document.get("images") or []
    links = parsed_document.get("links") or []

    compact: dict[str, Any] = {
        "page_count": parsed_document.get("page_count"),
        "pages": parsed_document.get("pages"),
        "spans": [
            compact_span(span)
            for span in spans
            if isinstance(span, dict) and span.get("text")
        ],
    }

    if images:
        compact["images"] = [
            compact_image(image)
            for image in images
            if isinstance(image, dict)
        ]

    if links:
        compact["links"] = [
            compact_link(link)
            for link in links
            if isinstance(link, dict)
        ]

    # Retain already-extracted table information if reasonably compact.
    tables = parsed_document.get("tables")
    if tables:
        compact["tables"] = tables

    return compact