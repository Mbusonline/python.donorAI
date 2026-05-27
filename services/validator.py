"""
Validator Service
Validates assembled markdown content for completeness
"""

import os
from typing import Dict, List, Tuple


def validate_markdown(
    markdown: str,
    image_mappings: List[Dict],
    csv_data: List[Dict] = None,
    template_sections: List[Dict] = None,
) -> Tuple[bool, List[str]]:
    """
    Validate that markdown content includes all required components.

    Args:
        markdown: The assembled markdown content
        image_mappings: List of image mappings with paths
        csv_data: Optional list of CSV data with chart paths
        template_sections: Optional list of template sections

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    # Check if markdown is not empty
    if not markdown or len(markdown.strip()) == 0:
        errors.append("Markdown content is empty")
        return False, errors

    # Check if all image files exist and are referenced
    if image_mappings:
        referenced_images = 0
        missing_images = []

        for img_mapping in image_mappings:
            img_path = img_mapping.get("path", "")

            # Check if file exists
            if not os.path.exists(img_path):
                missing_images.append(img_path)

            # Check if referenced in markdown (check both forward and backslash)
            normalized_path = img_path.replace("\\", "/")
            if normalized_path in markdown or img_path in markdown:
                referenced_images += 1

        if missing_images:
            errors.append(
                f"Missing image files: {', '.join(missing_images[:3])}{'...' if len(missing_images) > 3 else ''}"
            )

        if referenced_images == 0 and len(image_mappings) > 0:
            errors.append("No images referenced in markdown (expected at least some)")

    # Check if CSV data tables and charts are present
    if csv_data:
        missing_charts = []
        referenced_charts = 0

        for csv_item in csv_data:
            chart_path = csv_item.get("chart_path", "")

            # Check if chart file exists
            if chart_path and not os.path.exists(chart_path):
                missing_charts.append(chart_path)

            # Check if chart is referenced in markdown
            normalized_chart = chart_path.replace("\\", "/")
            if normalized_chart in markdown or chart_path in markdown:
                referenced_charts += 1

        if missing_charts:
            errors.append(
                f"Missing chart files: {', '.join(missing_charts[:3])}{'...' if len(missing_charts) > 3 else ''}"
            )

        if referenced_charts == 0 and len(csv_data) > 0:
            errors.append("No charts referenced in markdown (expected at least some)")

    # Check if all template sections are present
    if template_sections:
        missing_sections = []

        for section in template_sections:
            section_name = section.get("name", "")
            # Check if section heading exists in markdown (as # heading)
            if section_name and section_name not in markdown:
                missing_sections.append(section_name)

        if missing_sections:
            # Only warn if more than half are missing
            if len(missing_sections) > len(template_sections) / 2:
                errors.append(
                    f"Many sections missing: {', '.join(missing_sections[:3])}{'...' if len(missing_sections) > 3 else ''}"
                )

    # Check basic markdown structure
    if "##" not in markdown and "#" not in markdown:
        errors.append("No markdown headings found")

    # Validation result
    is_valid = len(errors) == 0

    if is_valid:
        print("✓ Markdown validation passed")
    else:
        print(f"✗ Markdown validation failed with {len(errors)} error(s)")
        for error in errors:
            print(f"  - {error}")

    return is_valid, errors
