"""
Prompts for image analysis with Gemini Vision
"""

IMAGE_DESCRIPTION_PROMPT = """You are analyzing an image for an NGO donor report.

TASK:
Analyze this image and return:

1. A brief description (1-2 sentences)
2. A professional caption suitable for a donor report

IMPORTANT:
- Be specific about what's happening in the image
- Captions should be formal yet engaging

Return your analysis as JSON with this exact structure:
{
  "description": "description here",
  "caption": "caption here"
}

Return ONLY the JSON object, no other text."""


IMAGE_SECTION_MAPPING_PROMPT = """You are mapping an image to the correct section of an NGO donor report.

AVAILABLE SECTIONS:
{available_sections}

SEED TEXT CONTEXT:
{seed_text}

IMAGE DESCRIPTION:
{image_description}

IMAGE CAPTION:
{image_caption}

TASK:
Choose the single best section for this image from AVAILABLE SECTIONS.

IMPORTANT:
- Consider context: activities go in "Project Activities", data visualizations in "Impact Data", portraits/group photos in "Gallery"
- You MUST pick a section name that appears exactly in AVAILABLE SECTIONS.

Return ONLY JSON with this exact structure:
{{
  "section": "section name"
}}

Return ONLY the JSON object, no other text."""
