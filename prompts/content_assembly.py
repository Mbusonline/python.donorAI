"""
Prompts for content assembly with OpenAI
"""

CONTENT_ASSEMBLY_PROMPT = """You are assembling a professional NGO donor report. 

REPORT STRUCTURE:
{sections_structure}

SEED TEXT (base content for each section):
{seed_text}

IMAGES (organized by section):
{images_data}

DATA TABLES & CHARTS:
{csv_data}

TEMPLATE IMAGE POSITIONS:
{template_image_positions}

TEMPLATE TEXT METRICS (for content length guidance):
{template_metrics}

TASK:
Create a complete, professional donor report in Markdown format following these guidelines:

1. **Structure**: Follow the exact section order and names from REPORT STRUCTURE above
2. **Content**: 
   - **IMPORTANT: Seed text is just a starting point - EXPAND IT SIGNIFICANTLY**
   - Use the seed text as the foundation for each section, but expand it 3-5x in length
   - Even if seed text is short, write detailed, comprehensive content for each section
   - Add context, background information, detailed descriptions, examples, and explanations
   - Enhance it to be engaging and professional
   - Keep the original facts and data intact
   - Rephrase the content to be more engaging and professional
   - Generate an appropriate title for the report.
   - For each section, write at least 3-5 detailed paragraphs even if seed text is only 1-2 sentences
   - Add supporting details, impact statements, and comprehensive explanations
3. **Images**: 
   - Reference images in appropriate sections using: `![Caption](image_path)`
   - Use the provided captions and descriptions
   - **CRITICAL: Preserve image positions from template**
   - If TEMPLATE IMAGE POSITIONS are provided:
     * Place images in the SAME ORDER as they appear in the template (first template image = first uploaded image)
     * If template shows image "before" text in a section, place it BEFORE the text in that section
     * If template shows image "after" text in a section, place it AFTER the text in that section
     * Match image positions based on the "position_type" field (before/after) and "context" field
   - Group related images together using markdown tables (2-3 images per row) ONLY if they appear together in template:
     ```
     | ![Caption 1](path1) | ![Caption 2](path2) |
     |---------------------|---------------------|
     ```
   - For single images, use regular markdown: `![caption](path)`
   - Maintain the exact sequence: template image order = uploaded image order
   - If no template positions available, place images logically within content according to section order and seed text.
4. **Data**: 
   - **CRITICAL: You MUST include ALL data tables and charts provided in the DATA TABLES & CHARTS section above**
   - For each CSV data item provided:
     * Include the data table using the markdown table format provided in the "table" field
     * Include the chart image using: `![Chart Description](chart_path)` where chart_path is the exact path from the "chart_path" field
     * Add brief analysis or summary of the data (use the "summary" field as a starting point)
   - Do not change the data in the tables or charts, just use them as is
   - **MANDATORY: Every table and chart from DATA TABLES & CHARTS must appear in the final report**
   - Place the tables and charts logically within content according to the section order and seed text
   - Use all the data in the tables and charts to create the report
   - If multiple CSV items are provided, include ALL of them in the report
5. **Formatting**:
   - Use proper Markdown headings (# ## ###)
   - Use bullet points and numbered lists appropriately
   - Keep paragraphs concise and readable
   - Add emphasis (bold/italic) for key points
6. **Tone**: Professional yet warm, suitable for donors

- **CRITICAL: Match template length - THIS IS MANDATORY**
  - If TEMPLATE TEXT METRICS are provided, you MUST generate content that matches or exceeds the template's length
  - Target word count: Aim for at least the template's word_count (or MORE - preferably 10-20% more)
  - Target character count: Aim for at least the template's character_count (or MORE)
  - **DO NOT stop writing until you reach the target length**
  - Expand each section with detailed, engaging content to reach the target length
  - Add more paragraphs, examples, detailed descriptions, case studies, and comprehensive explanations
  - Look at the TEMPLATE TEXT METRICS and ensure your generated report has similar or greater word_count and character_count
  - If template has X words, your report MUST have at least X words (preferably 1.1X to 1.2X)
  - If seed text is short, that's fine - expand it significantly with detailed content
  - Write comprehensive paragraphs with full context, background, and detailed explanations
  - Add multiple paragraphs per section, even if seed text is brief
- Make atleast 5 pages in the report. Try to make the report as long as possible.
- **Length is more important than brevity - write extensively to match template size**
- Use all the data (images, tables, charts, seed text, sections, csv data, image mappings, template sections) provided to create the report.

OUTPUT: Return ONLY the complete Markdown report, no explanations or meta-commentary.
"""
