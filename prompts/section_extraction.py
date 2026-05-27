"""
Prompts for section extraction from templates
"""

SECTION_EXTRACTION_PROMPT = """You are analyzing a document to extract ONLY the main section headings.

TASK: Identify the main section headings (like "Introduction", "Key Highlights", "Project Activities", "Impact Data", etc.) and also identify the home page heading and ignore:
- Headers/footers (like organization name, date, page numbers)
- Subheadings within sections
- Text within paragraphs
- Dates, addresses, or metadata

Return ONLY a JSON array with this exact format:
[
  {{"name": "Section Name", "order": 1}},
  {{"name": "Section Name", "order": 2}}
]

Return ONLY the JSON array, no other text.

Document text:
{document_text}"""
