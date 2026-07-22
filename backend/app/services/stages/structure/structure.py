from __future__ import annotations

import json
from typing import Any

from ...openai_client import get_openai_client
from .prompt import PROMPT
from .compact import compact_layout_json


def build_ideal_structure(
    markdown: str,
    parsed_document: dict[str, Any],
) -> dict[str, Any]:
    client = get_openai_client()

    compact_json = compact_layout_json(parsed_document)

    # separators removes unnecessary spaces and line breaks.
    compact_json_text = json.dumps(
        compact_json,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    full_prompt = f"""
{PROMPT}

<MARKDOWN>
{markdown}
</MARKDOWN>

<LAYOUT_JSON>
{compact_json_text}
</LAYOUT_JSON>
""".strip()

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=4000,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": full_prompt,
            }
        ],
    )

    result = response.choices[0].message.content

    if not result:
        raise ValueError("The model returned an empty response.")

    return json.loads(result)