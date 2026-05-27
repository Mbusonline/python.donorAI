"""
Template Structure Extraction Service
Extracts section headings from PDF or DOCX template files using Gemini AI
"""

import json
import os
from typing import Dict, List

import fitz  # PyMuPDF
import google.generativeai as genai
from docx import Document

import settings
from prompts.section_extraction import SECTION_EXTRACTION_PROMPT
from services.api_logger import log_gemini_api_call


def extract_text_from_pdf(file_path: str) -> str:
    """Extract all text from PDF file."""
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    doc.close()
    return text


def extract_text_from_docx(file_path: str) -> str:
    """Extract all text from DOCX file."""
    doc = Document(file_path)
    text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
    return text


def extract_image_positions_from_pdf(file_path: str) -> List[Dict]:
    """
    Extract image positions from PDF template to preserve layout.
    
    Args:
        file_path: Path to PDF template file
        
    Returns:
        List of dictionaries with image position information
    """
    doc = fitz.open(file_path)
    image_positions = []
    
    print(f"Extracting image positions from PDF template: {file_path}")
    
    for page_num, page in enumerate(doc):
        # Get all images on this page
        image_list = page.get_images()
        
        # Get text blocks to understand context
        text_blocks = page.get_text("blocks")
        
        for img_index, img in enumerate(image_list):
            try:
                # Get image rectangles (positions on page)
                image_rects = page.get_image_rects(img[0])
                
                for rect in image_rects:
                    # Find nearby text blocks to determine context
                    text_before = []
                    text_after = []
                    
                    for block in text_blocks:
                        block_rect = fitz.Rect(block[:4])
                        block_text = block[4] if len(block) > 4 else ""
                        
                        # Text before image (above or to the left)
                        if block_rect.y1 < rect.y0 or (block_rect.y1 <= rect.y0 + 20 and block_rect.x1 < rect.x0):
                            text_before.append(block_text)
                        # Text after image (below or to the right)
                        elif block_rect.y0 > rect.y1 or (block_rect.y0 >= rect.y1 - 20 and block_rect.x0 > rect.x1):
                            text_after.append(block_text)
                    
                    # Determine position relative to text
                    text_before_str = " ".join(text_before[-3:])  # Last 3 blocks before
                    text_after_str = " ".join(text_after[:3])  # First 3 blocks after
                    
                    # Determine if image is before or after text in flow
                    position_type = "after" if len(text_before_str) > len(text_after_str) else "before"
                    
                    # Get section context from nearby text
                    context_text = (text_before_str[-200:] + " " + text_after_str[:200]).strip()
                    
                    image_positions.append({
                        "page": page_num + 1,
                        "image_index": len(image_positions),
                        "position_type": position_type,  # "before" or "after" text
                        "y_position": rect.y0,  # Vertical position on page
                        "context": context_text,  # Nearby text for section matching
                    })
            except Exception as e:
                print(f"Warning: Could not extract image {img_index} from page {page_num + 1}: {str(e)}")
                continue
    
    doc.close()
    print(f"Extracted {len(image_positions)} image positions from template")
    return image_positions


def extract_sections_with_gemini(
    text_content: str, log_dir: str = "logs", output_dir: str = "output"
) -> List[Dict]:
    """Use Gemini AI to identify section headings from document text."""
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in settings")

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    # Limit text content to avoid token limits
    limited_text = text_content[: settings.MAX_TEXT_LENGTH]

    # Format prompt with document text
    full_prompt = SECTION_EXTRACTION_PROMPT.format(document_text=limited_text)

    # save in the unique id directory

    with open(os.path.join(output_dir, "full_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(full_prompt)

    try:
        response = model.generate_content(full_prompt)
        result_text = response.text.strip()

        # Extract token usage if available
        input_tokens = None
        output_tokens = None
        if hasattr(response, "usage_metadata"):
            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", None)
            output_tokens = getattr(usage, "candidates_token_count", None)

        # Extract JSON from response (in case there's extra text)
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        sections = json.loads(result_text)
        print(f"Sections: {sections}")

        # Log API call
        log_gemini_api_call(
            model=settings.GEMINI_MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_length=len(full_prompt) if input_tokens is None else None,
            response_length=len(result_text) if output_tokens is None else None,
            operation="section_extraction",
            success=True,
            log_dir=log_dir,
        )

        return sections if isinstance(sections, list) else []
    except Exception as e:
        error_msg = str(e)
        # Log failed API call
        log_gemini_api_call(
            model=settings.GEMINI_MODEL,
            prompt_length=len(full_prompt),
            operation="section_extraction",
            success=False,
            error=error_msg,
            log_dir=log_dir,
        )
        raise ValueError(f"Error extracting sections with Gemini: {error_msg}")


def extract_sections(file_path: str, log_dir: str = "logs") -> Dict:
    """
    Extract section headings from template file using Gemini AI.

    Args:
        file_path: Path to template file (PDF or DOCX)

    Returns:
        Dictionary with 'sections' key containing list of sections

    Raises:
        ValueError: If file format is not supported
        FileNotFoundError: If file doesn't exist
    """
    print(f"Extracting sections from {file_path}")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Template file not found: {file_path}")

    file_ext = os.path.splitext(file_path)[1].lower()

    # Extract text content
    if file_ext == ".pdf":
        text_content = extract_text_from_pdf(file_path)
        # Also extract image positions from PDF
        template_image_positions = extract_image_positions_from_pdf(file_path)
    elif file_ext in [".docx", ".doc"]:
        text_content = extract_text_from_docx(file_path)
        # DOCX image position extraction not implemented yet
        template_image_positions = []
    else:
        raise ValueError(f"Unsupported file format: {file_ext}. Use .pdf or .docx")

    # Calculate template text metrics for content length guidance
    template_metrics = {
        "character_count": len(text_content),
        "word_count": len(text_content.split()),
        "paragraph_count": len([p for p in text_content.split("\n\n") if p.strip()]),
        "line_count": len([l for l in text_content.split("\n") if l.strip()]),
    }
    
    print(f"Template text metrics: {template_metrics['word_count']} words, {template_metrics['character_count']} characters")

    # Use Gemini to identify sections
    sections = extract_sections_with_gemini(text_content, log_dir, output_dir="output")

    save_sections_to_json(sections, "sections.json", output_dir="output")

    return {
        "sections": sections,
        "template_image_positions": template_image_positions,
        "template_metrics": template_metrics
    }


def save_sections_to_json(
    sections_dict: Dict, output_path: str, output_dir: str = "output"
):
    """Save extracted sections to a JSON file."""
    with open(os.path.join(output_dir, "sections.json"), "w", encoding="utf-8") as f:
        json.dump(sections_dict, f, indent=2, ensure_ascii=False)

    print(f"Sections saved to {output_path}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python template_extractor.py <template_file_path>")
        sys.exit(1)

    template_path = sys.argv[1]

    try:
        result = extract_sections(template_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)
