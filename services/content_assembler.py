"""
Content Assembly Service
Assembles final report content using OpenAI or Google Gemini (from tbl_model at call site).
"""

import json
import os
import time
from typing import Dict, List, Tuple

from openai import OpenAI

import settings
from prompts.content_assembly import CONTENT_ASSEMBLY_PROMPT
from services.api_logger import log_gemini_api_call, log_openai_api_call

_SYSTEM = (
    "You are a professional report writer for NGOs. "
    "Create well-structured, engaging donor reports."
)


def _is_transient_gemini_assembly_error(err: BaseException) -> bool:
    """504 / overload / deadline — safe to retry the same request."""
    msg = str(err).lower()
    if any(
        s in msg
        for s in (
            "504",
            "503",
            "502",
            "429",
            "timeout",
            "timed out",
            "deadline",
            "unavailable",
            "overload",
            "try again",
            "resource exhausted",
        )
    ):
        return True
    try:
        from google.api_core import exceptions as google_exc

        return isinstance(
            err,
            (
                google_exc.DeadlineExceeded,
                google_exc.ServiceUnavailable,
                google_exc.InternalServerError,
                google_exc.BadGateway,
                google_exc.GatewayTimeout,
                google_exc.ResourceExhausted,
            ),
        )
    except ImportError:
        return False


def _strip_markdown_fences(markdown_content: str) -> str:
    markdown_content = (markdown_content or "").strip()
    if "```markdown" in markdown_content:
        return markdown_content.split("```markdown", 1)[1].split("```", 1)[0].strip()
    if "```" in markdown_content:
        return markdown_content.split("```", 1)[1].split("```", 1)[0].strip()
    return markdown_content


def build_assembly_user_prompt(
    template_sections: List[Dict],
    seed_text: Dict,
    image_mappings: List[Dict],
    csv_data: List[Dict] = None,
    template_image_positions: List[Dict] = None,
    template_metrics: Dict = None,
) -> str:
    sections_list = []
    for section in sorted(template_sections, key=lambda x: x.get("order", 0)):
        section_name = section.get("name", "")
        sections_list.append(f"{section.get('order', 0)}. {section_name}")
    sections_structure = "\n".join(sections_list)
    seed_text_formatted = json.dumps(seed_text, indent=2, ensure_ascii=False)

    images_by_section = {}
    for img in image_mappings:
        section = img.get("section", "Unknown")
        if section not in images_by_section:
            images_by_section[section] = []
        images_by_section[section].append(
            {
                "description": img.get("description", ""),
                "caption": img.get("caption", ""),
                "path": img.get("path", ""),
            }
        )
    images_formatted = json.dumps(images_by_section, indent=2, ensure_ascii=False)

    csv_by_section = {}
    if csv_data:
        for csv_item in csv_data:
            section = csv_item.get("target_section", "Impact Data")
            if section not in csv_by_section:
                csv_by_section[section] = []
            csv_by_section[section].append(
                {
                    "table": csv_item.get("table_markdown", ""),
                    "chart_path": csv_item.get("chart_path", ""),
                    "summary": csv_item.get("summary", ""),
                }
            )
    csv_formatted = json.dumps(csv_by_section, indent=2, ensure_ascii=False)

    if template_image_positions:
        positions_formatted = json.dumps(template_image_positions, indent=2, ensure_ascii=False)
    else:
        positions_formatted = "No template image positions available"

    if template_metrics:
        metrics_formatted = json.dumps(template_metrics, indent=2, ensure_ascii=False)
    else:
        metrics_formatted = "No template metrics available"

    return CONTENT_ASSEMBLY_PROMPT.format(
        sections_structure=sections_structure,
        seed_text=seed_text_formatted,
        images_data=images_formatted,
        csv_data=csv_formatted if csv_data else "No CSV data provided",
        template_image_positions=positions_formatted,
        template_metrics=metrics_formatted,
    )


def assemble_report_with_model(
    template_sections: List[Dict],
    seed_text: Dict,
    image_mappings: List[Dict],
    csv_data: List[Dict] = None,
    template_image_positions: List[Dict] = None,
    template_metrics: Dict = None,
    output_dir: str = "output",
    log_dir: str = "logs",
    api_key: str = "",
    model_name: str = "",
    provider: str = "openai",
) -> Tuple[str, Dict]:
    """
    Assemble report using the given provider and credentials.
    Returns (markdown, usage_dict) where usage_dict includes
    provider, model, operation, input_tokens, output_tokens.
    """
    prov = (provider or "openai").strip().lower()
    if prov in ("gemini", "google"):
        prov = "google"

    prompt = build_assembly_user_prompt(
        template_sections=template_sections,
        seed_text=seed_text,
        image_mappings=image_mappings,
        csv_data=csv_data,
        template_image_positions=template_image_positions,
        template_metrics=template_metrics,
    )

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "assembly_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)

    if prov == "openai":
        if not api_key:
            raise ValueError("OpenAI api_key is required for content assembly")
        client = OpenAI(api_key=api_key)
        try:
            response = client.chat.completions.create(
                model=model_name or settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
            )
            input_tokens = None
            output_tokens = None
            if hasattr(response, "usage") and response.usage:
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens
            raw = (response.choices[0].message.content or "").strip()
            markdown_content = _strip_markdown_fences(raw)
            total_prompt_length = len(prompt) + len(_SYSTEM)
            log_openai_api_call(
                model=model_name or settings.OPENAI_MODEL,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                prompt_length=total_prompt_length if input_tokens is None else None,
                response_length=len(markdown_content) if output_tokens is None else None,
                operation="content_assembly",
                success=True,
                log_dir=log_dir,
            )
            usage = {
                "operation": "content_assembly",
                "provider": "openai",
                "model": model_name or settings.OPENAI_MODEL,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
        except Exception as e:
            err = str(e)
            log_openai_api_call(
                model=model_name or settings.OPENAI_MODEL,
                prompt_length=len(prompt) + len(_SYSTEM),
                operation="content_assembly",
                success=False,
                error=err,
                log_dir=log_dir,
            )
            raise ValueError(f"Error assembling report with OpenAI: {err}") from e

    elif prov == "google":
        if not api_key:
            raise ValueError("Google/Gemini api_key is required for content assembly")
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        mname = model_name or settings.GEMINI_MODEL
        # Backward-compatible with google-generativeai versions that do not support
        # `system_instruction` in GenerativeModel constructor.
        model = genai.GenerativeModel(model_name=mname)
        gemini_prompt = f"{_SYSTEM}\n\n{prompt}"
        max_attempts = settings.GEMINI_ASSEMBLY_MAX_RETRIES
        base_delay = settings.GEMINI_ASSEMBLY_RETRY_BASE_DELAY
        for attempt in range(max_attempts):
            try:
                response = model.generate_content(gemini_prompt)
                raw = (response.text or "").strip()
                markdown_content = _strip_markdown_fences(raw)
                input_tokens = None
                output_tokens = None
                if hasattr(response, "usage_metadata"):
                    usage_md = response.usage_metadata
                    input_tokens = getattr(usage_md, "prompt_token_count", None)
                    output_tokens = getattr(usage_md, "candidates_token_count", None)
                log_gemini_api_call(
                    model=mname,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    prompt_length=len(gemini_prompt) if input_tokens is None else None,
                    response_length=len(markdown_content) if output_tokens is None else None,
                    operation="content_assembly",
                    success=True,
                    log_dir=log_dir,
                )
                usage = {
                    "operation": "content_assembly",
                    "provider": "google",
                    "model": mname,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
                break
            except Exception as e:
                will_retry = (
                    _is_transient_gemini_assembly_error(e) and attempt < max_attempts - 1
                )
                if will_retry:
                    wait_s = base_delay * (2**attempt)
                    print(
                        f"[content_assembly] Gemini transient error "
                        f"(attempt {attempt + 1}/{max_attempts}): {e!s}; "
                        f"retrying in {wait_s:.1f}s"
                    )
                    time.sleep(wait_s)
                    continue
                err = str(e)
                log_gemini_api_call(
                    model=model_name or settings.GEMINI_MODEL,
                    prompt_length=len(gemini_prompt),
                    operation="content_assembly",
                    success=False,
                    error=err,
                    log_dir=log_dir,
                )
                raise ValueError(f"Error assembling report with Gemini: {err}") from e
    else:
        raise ValueError(f"Unsupported assembly provider: {provider!r} (use openai or google)")

    markdown_path = os.path.join(output_dir, "assembled_report.md")
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    print(f"Report assembled: {len(markdown_content)} characters -> {markdown_path}")

    return markdown_content, usage
