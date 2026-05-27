"""
Map document images to template sections using caption/description only (Gemini text).
"""

import json
from typing import Dict, List, Optional, Tuple

import google.generativeai as genai

import settings
from prompts.image_analysis import IMAGE_SECTION_MAPPING_PROMPT
from services.api_logger import log_gemini_api_call


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if "```json" in text:
        return text.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def map_image_to_section_text_only(
    *,
    template_sections: List[Dict],
    seed_text: Dict,
    description: str,
    caption: str,
    log_dir: str,
    document_id: Optional[str] = None,
) -> Tuple[str, Dict]:
    """
    Choose a section name from template_sections using Gemini (no vision).
    Returns (section_name, usage dict for cost tracking).
    """
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in settings (required for section mapping)")

    section_names = [s.get("name", s) if isinstance(s, dict) else str(s) for s in template_sections]
    available_sections = "\n".join(f"- {name}" for name in section_names)
    seed_context = json.dumps(seed_text, indent=2, ensure_ascii=False) if seed_text else "No seed text provided"

    map_prompt = IMAGE_SECTION_MAPPING_PROMPT.format(
        available_sections=available_sections,
        seed_text=seed_context,
        image_description=description or "Image description unavailable",
        image_caption=caption or "Image from donor report",
    )

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    response = model.generate_content(map_prompt)
    result_text = _extract_json(response.text)

    input_tokens = None
    output_tokens = None
    if hasattr(response, "usage_metadata"):
        usage = response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", None)
        output_tokens = getattr(usage, "candidates_token_count", None)

    log_gemini_api_call(
        model=settings.GEMINI_MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        prompt_length=len(map_prompt) if input_tokens is None else None,
        response_length=len(result_text) if output_tokens is None else None,
        operation="image_section_mapping_text_only",
        success=True,
        log_dir=log_dir,
    )

    try:
        map_obj = json.loads(result_text) if result_text else {}
    except json.JSONDecodeError:
        map_obj = {}

    section = (map_obj.get("section") or "").strip()
    if not section or (section_names and section not in section_names):
        section = section_names[0] if section_names else "Gallery"

    usage = {
        "operation": "section_mapping",
        "provider": "google",
        "model": settings.GEMINI_MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if document_id is not None:
        usage["document_id"] = document_id

    return section, usage
