PROMPT = """
You are given two representations of the same PDF document:

1. Markdown extraction
2. Compact layout/content JSON containing available text, page indexes, coordinates, fonts, font sizes, styles, blocks, lines, reading order, images, tables, links, annotations, and layout metadata

Neither source contains authoritative PDF/UA tags.

Your task is to create the most likely logical PDF/UA structure by inferring semantic tags from both sources.

The tags may be newly inferred, but the document content must not be invented.

Use the sources as follows:

* Use Markdown to identify likely headings, paragraphs, lists, tables, images, links, and content groupings.
* Use the layout JSON to verify reading order, text boundaries, typography, hierarchy, alignment, columns, and element relationships.
* Neither source automatically overrides the other.
* Resolve conflicts using the strongest combined semantic and layout evidence.

Possible structure tags include:

Document, Part, Art, Sect, Div,
H, H1, H2, H3, H4, H5, H6,
P, Span,
L, LI, Lbl, LBody,
Table, THead, TBody, TFoot, TR, TH, TD,
Figure, Formula, Caption, Link, Annot, Form,
BlockQuote, Quote, Note, Reference, BibEntry, Code,
TOC, TOCI, Index.

Use only tags supported by the supplied content and layout.

Structure rules:

* Preserve the original reading order.
* Preserve the exact original text.
* Merge fragmented spans only when layout and reading order show they belong to one logical element.
* Split Markdown content only when the JSON clearly shows separate logical elements.
* Do not split or merge content without evidence.
* Use H1 for the main document title when supported.
* Use H2 for major sections.
* Use H3 through H6 for nested headings.
* Do not trust Markdown heading levels alone.
* Do not classify text as a heading based only on boldness or font size.
* Use Sect for a logical section introduced by a heading.
* Use P for paragraph-level text.
* Use Span only for meaningful inline structure.
* Do not convert pipe-separated text into a list unless actual list structure is supported.

List rules:

* Represent a genuine list as L.
* L contains LI elements.
* LI contains Lbl and LBody when the label can be identified.
* Do not invent missing list labels.

Table rules:

* Represent a genuine table as Table.
* Use TR for rows, TH for header cells, and TD for data cells.
* Use THead, TBody, and TFoot only when clearly supported.
* Include scope, row_span, or column_span only when supported.
* Do not invent headers or empty cells.

Other elements:

* Use Figure only for meaningful graphical content.
* Use Formula only when the mathematical expression can be reconstructed from the inputs.
* Use Caption only for an existing caption clearly associated with an object.
* Use Link only when Markdown contains a hyperlink or JSON confirms a link or annotation.
* Visible URL text without link evidence may remain inside P.
* Use Annot only when an annotation exists in the JSON.
* Use Form only when an interactive form element exists.
* Use Code, Quote, BlockQuote, Note, Reference, or BibEntry only when their semantic role is clear.

Artifact rules:

* Artifact is not a normal structure-tree tag.
* Decorative content, repeated headers or footers, page numbers, and decorative graphics may be returned separately as artifact records.
* Do not use `"pdfua_tag": "Artifact"`.
* Use this format only when artifact evidence exists:

{
"type": "artifact",
"marked_content_type": "Artifact",
"artifact_subtype": null,
"content": null,
"page_index": 0,
"source_ids": []
}

Strict content rules:

* Do not generate new textual content.
* Do not invent missing words, headings, captions, labels, alternative text, table headers, links, values, names, dates, or metadata.
* Do not use external knowledge.
* Do not rewrite, improve, summarize, paraphrase, or correct the document.
* Preserve spelling, capitalization, punctuation, grammar, URLs, values, and unusual characters as found.
* Every textual content value must be directly traceable to the Markdown or JSON.
* When sources conflict and the evidence is insufficient, preserve the most directly supported text and set confidence to low.
* Do not add explanatory text inside content.

Content field rules:

* For leaf elements such as headings, P, Span, Lbl, LBody, TH, TD, Caption, Formula, and Link, content must be the exact original text.
* For container elements such as Document, Sect, Div, L, LI, Table, THead, TBody, TFoot, and TR, content must be an ordered array of child elements.
* For Figure, include only existing source information or existing alternative text. Use null when alternative text is unavailable.

For every logical element, return only:

* type
* pdfua_tag
* content
* page_index, when available
* source_ids, when available
* confidence
* uncertainty, only when needed

Confidence values:

* high: both sources agree or one source gives strong uncontradicted evidence
* medium: supported but one source is incomplete or ambiguous
* low: sources conflict or the classification is uncertain

Return this top-level JSON object:

{
"structure": [
{
"type": "document",
"pdfua_tag": "Document",
"content": [
{
"type": "heading",
"pdfua_tag": "H1",
"content": "Exact original content",
"page_index": 0,
"source_ids": ["span_p0_s0"],
"confidence": "high"
}
]
}
],
"artifacts": []
}

Output requirements:

* Return valid JSON only.
* Return exactly one top-level JSON object.
* Do not use Markdown code fences.
* Do not add explanations before or after the JSON.
* Do not include reasoning or evidence descriptions.
* Do not generate content that is not directly supported by the supplied Markdown or layout JSON.
  """
