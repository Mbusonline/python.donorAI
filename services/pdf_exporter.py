"""
PDF Exporter Service
Converts markdown content to PDF using markdown2 and reportlab
"""

import os
import re
from typing import Dict

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Image as RLImage,
)
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfgen import canvas as pdf_canvas

import settings


def _sanitize_paragraph_markup(text: str) -> str:
    """
    Normalize lightweight HTML from assembled markdown for ReportLab Paragraph.

    ReportLab's parser requires self-closing <br/>; LLM output often uses <br>.
    """
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r"<br\s*/?>", "<br/>", t, flags=re.IGNORECASE)
    t = re.sub(r"</br\s*>", "", t, flags=re.IGNORECASE)
    return t


def _ensure_bordered_image(
    image_path: str,
    *,
    border_width: int | None = None,
    border_color: str | None = None,
    cache_dir: str | None = None,
) -> str:
    """
    Ensure an image has a border by writing a bordered copy and returning its path.
    Falls back to the original path if anything goes wrong.
    """
    try:
        if border_width is None:
            border_width = settings.IMAGE_BORDER_WIDTH
        if border_color is None:
            border_color = settings.IMAGE_BORDER_COLOR
        if not image_path or not os.path.exists(image_path):
            return image_path
        base = os.path.basename(image_path)
        # Avoid double-bordering for already processed assets.
        if "_bordered" in os.path.splitext(base)[0].lower():
            return image_path

        out_dir = cache_dir if cache_dir and os.path.isdir(cache_dir) else os.path.dirname(image_path)
        name, ext = os.path.splitext(base)
        out_path = os.path.join(out_dir, f"{name}_bordered{ext}")

        # Reuse if already created.
        if os.path.exists(out_path):
            return out_path

        from PIL import Image as PILImage
        from PIL import ImageOps

        img = PILImage.open(image_path)
        if img.mode in ("RGBA", "LA", "P"):
            # Flatten transparency for consistent borders.
            background = PILImage.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            if img.mode in ("RGBA", "LA"):
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        bordered = ImageOps.expand(img, border=border_width, fill=border_color)
        bordered.save(out_path, quality=95)
        return out_path
    except Exception:
        return image_path


def create_front_page(
    canvas,
    doc,
    report_title: str = "Donor Report",
    report_subtitle: str = None,
    report_date: str = None,
    location: str = None,
    report_type: str = None,
    funder_logo_path: str = None,
    funder_name: str = None,
    front_page_image_path: str = None,
    magic_bus_logo_path: str = None,
    page_size: tuple = A4,
):
    """
    Create a professional front page matching the template design.
    
    Args:
        canvas: ReportLab canvas object
        doc: Document object
        report_title: Main title (e.g., "Bridgeshala")
        report_subtitle: Subtitle (e.g., "Empowering Education through Adolescent Education")
        report_date: Date/period (e.g., "July-September 2025")
        location: Location (e.g., "Pithampur, Dhar, Madhya Pradesh")
        report_type: Type of report (e.g., "Quarterly Progress Report")
        funder_logo_path: Path to funder logo image (optional)
        funder_name: Name of the funder (optional)
        front_page_image_path: Path to large image for center (optional)
        magic_bus_logo_path: Path to Magic Bus logo (optional)
        page_size: Page size tuple (width, height)
    """
    canvas.saveState()
    
    page_width, page_height = page_size
    
    # White background (clean, professional)
    canvas.setFillColor(colors.white)
    canvas.rect(0, 0, page_width, page_height, fill=1, stroke=0)
    
    # Layout constants
    margin_x = 2 * cm
    title_color = colors.HexColor("#000000")

    # Keep both logos the same size so they don't collide with long titles.
    standard_logo_height = 2.4 * cm
    standard_logo_width = 3.4 * cm

    # Logo band at the top (same for both logos)
    logo_top_y = page_height - 1.2 * cm
    logo_bottom_y = logo_top_y - standard_logo_height

    def _wrap_text_to_width(text: str, max_w: float, *, font_name: str, font_size: int) -> list[str]:
        """Greedy word wrap based on rendered width."""
        raw = (text or "").strip()
        if not raw:
            return [""]
        words = raw.split()
        lines: list[str] = []
        cur: list[str] = []
        for w in words:
            trial = (" ".join(cur + [w])).strip()
            if trial and stringWidth(trial, font_name, font_size) <= max_w:
                cur.append(w)
                continue
            if cur:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                # Single word longer than max width; avoid infinite loop.
                lines.append(w)
                cur = []
        if cur:
            lines.append(" ".join(cur))
        return lines

    def _draw_auto_title(text: str, *, center_x: float, top_y: float, max_w: float) -> float:
        """
        Draw a title that never clips: auto-shrink and wrap (up to 2 lines).
        Returns y just below the title block.
        """
        font_name = "Helvetica-Bold"
        max_size = 30
        min_size = 16
        line_gap = 0.2 * cm

        title = (text or "Donor Report").strip()

        # 1) Try single-line auto-shrink.
        size = max_size
        while size > min_size and stringWidth(title, font_name, size) > max_w:
            size -= 1

        if stringWidth(title, font_name, size) <= max_w:
            canvas.setFont(font_name, size)
            canvas.setFillColor(title_color)
            canvas.drawCentredString(center_x, top_y, title)
            return top_y - (size * 1.15) - line_gap

        # 2) Wrap to <= 2 lines.
        size = 22
        while size > min_size:
            lines = _wrap_text_to_width(title, max_w, font_name=font_name, font_size=size)
            if len(lines) <= 2:
                canvas.setFont(font_name, size)
                canvas.setFillColor(title_color)
                y = top_y
                for line in lines:
                    canvas.drawCentredString(center_x, y, line)
                    y -= (size * 1.15)
                return y - line_gap
            size -= 1

        # 3) Last resort: draw first 2 wrapped lines at min size.
        lines = _wrap_text_to_width(title, max_w, font_name=font_name, font_size=min_size)[:2]
        canvas.setFont(font_name, min_size)
        canvas.setFillColor(title_color)
        y = top_y
        for line in lines:
            canvas.drawCentredString(center_x, y, line)
            y -= (min_size * 1.15)
        return y - line_gap
    
    # Magic Bus Logo (top left) - Constant
    if magic_bus_logo_path and os.path.exists(magic_bus_logo_path):
        try:
            # Use the same standard size as funder logo
            mb_logo_width = standard_logo_width
            mb_logo_height = standard_logo_height
            
            # Draw logo image - align bottom of logo with logo_bottom_y
            mb_logo_x = margin_x
            mb_logo_y = logo_bottom_y
            
            canvas.drawImage(
                magic_bus_logo_path, 
                mb_logo_x, 
                mb_logo_y, 
                width=mb_logo_width, 
                height=mb_logo_height, 
                preserveAspectRatio=True,
                mask='auto'  # Handle transparency if present
            )
            print(f"✓ Added Magic Bus logo: {os.path.basename(magic_bus_logo_path)} (size: {mb_logo_width/cm:.2f}cm x {mb_logo_height/cm:.2f}cm)")
        except Exception as e:
            import traceback
            print(f"Warning: Could not load Magic Bus logo: {str(e)}")
            print(traceback.format_exc())
            # Fallback: Text logo
            canvas.setFont("Helvetica-Bold", 16)
            canvas.setFillColor(colors.HexColor("#2980b9"))
            canvas.drawString(2 * cm, logo_bottom_y, "Magic Bus")
            canvas.setFont("Helvetica", 9)
            canvas.setFillColor(colors.HexColor("#2c3e50"))
            canvas.drawString(2 * cm, logo_bottom_y - 0.5 * cm, "Childhood to Livelihood")
    else:
        # Fallback: Text logo
        canvas.setFont("Helvetica-Bold", 16)
        canvas.setFillColor(colors.HexColor("#2980b9"))
        canvas.drawString(2 * cm, logo_bottom_y, "Magic Bus")
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#2c3e50"))
        canvas.drawString(2 * cm, logo_bottom_y - 0.5 * cm, "Childhood to Livelihood")
    
    # Funder Logo (top right) - Variable
    if funder_logo_path and os.path.exists(funder_logo_path):
        try:
            # Use EXACT same standard size for funder logo
            funder_logo_width = standard_logo_width
            funder_logo_height = standard_logo_height
            
            # Draw funder logo - align bottom of logo with logo_bottom_y (same position as Magic Bus logo)
            funder_logo_x = page_width - margin_x - funder_logo_width  # Right-aligned
            funder_logo_y = logo_bottom_y
            
            canvas.drawImage(
                funder_logo_path, 
                funder_logo_x, 
                funder_logo_y, 
                width=funder_logo_width, 
                height=funder_logo_height, 
                preserveAspectRatio=True,
                mask='auto'  # Handle transparency if present
            )
            print(f"✓ Added funder logo: {os.path.basename(funder_logo_path)} (size: {funder_logo_width/cm:.2f}cm x {funder_logo_height/cm:.2f}cm)")
        except Exception as e:
            print(f"Warning: Could not load funder logo: {str(e)}")
    
    # Main Title (centered, below logos)
    max_title_width = page_width - (2 * margin_x)
    title_top_y = logo_bottom_y - 1.2 * cm
    after_title_y = _draw_auto_title(
        report_title,
        center_x=page_width / 2.0,
        top_y=title_top_y,
        max_w=max_title_width,
    )
    
    # Subtitle (below main title)
    if report_subtitle:
        canvas.setFont("Helvetica-Bold", 14)
        canvas.setFillColor(colors.HexColor("#2c3e50"))
        canvas.drawCentredString(page_width / 2.0, after_title_y, report_subtitle)
    
    # CSR Initiative text (below subtitle)
    if funder_name:
        canvas.setFont("Helvetica-Oblique", 12)
        canvas.setFillColor(colors.HexColor("#666666"))
        initiative_text = f"A CSR Initiative of {funder_name}"
        canvas.drawCentredString(page_width / 2.0, after_title_y - 1 * cm, initiative_text)
    
    # Large central image (if provided)
    if front_page_image_path and os.path.exists(front_page_image_path):
        try:
            # Ensure cover image has a border (write alongside or in output folder when provided).
            front_page_image_path = _ensure_bordered_image(front_page_image_path)
            # Calculate image position (center of page, below title area)
            # First, get image dimensions to calculate proper centering
            from PIL import Image as PILImage
            pil_img = PILImage.open(front_page_image_path)
            img_actual_width, img_actual_height = pil_img.size
            img_aspect_ratio = img_actual_width / img_actual_height
            
            # Set desired dimensions
            img_height = 12 * cm
            img_width = img_height * img_aspect_ratio
            
            # If image is too wide, constrain by width instead
            max_width = 16 * cm
            if img_width > max_width:
                img_width = max_width
                img_height = img_width / img_aspect_ratio
            
            # Center horizontally and vertically
            img_x = (page_width - img_width) / 2.0
            img_y_start = (page_height - img_height) / 2.0 - 1 * cm  # Slightly above center
            
            canvas.drawImage(front_page_image_path, img_x, img_y_start,
                           width=img_width, height=img_height, preserveAspectRatio=True)
            print(f"✓ Added front page image (centered): {os.path.basename(front_page_image_path)}")
        except Exception as e:
            print(f"Warning: Could not load front page image: {str(e)}")
    
    # Location (below image or below title if no image)
    if location:
        # Position relative to title/subtitle block (title_y was removed by auto-fit logic).
        location_y = after_title_y - (
            18 * cm if front_page_image_path and os.path.exists(front_page_image_path) else 4 * cm
        )
        canvas.setFont("Helvetica", 11)
        canvas.setFillColor(colors.HexColor("#2c3e50"))
        # Handle multi-line location
        location_lines = location.split(',')
        for i, line in enumerate(location_lines):
            canvas.drawCentredString(page_width / 2.0, location_y - (i * 0.6 * cm), line.strip())
    
    # Report Type and Date (centered, near bottom)
    bottom_y = 4 * cm
    if report_type:
        canvas.setFont("Helvetica-Bold", 12)
        canvas.setFillColor(colors.HexColor("#000000"))
        canvas.drawCentredString(page_width / 2.0, bottom_y + 1 * cm, report_type)
    
    if report_date:
        canvas.setFont("Helvetica-Bold", 12)
        canvas.setFillColor(colors.HexColor("#000000"))
        canvas.drawCentredString(page_width / 2.0, bottom_y, report_date)
    
    # Implementation Partner (at very bottom)
    canvas.setFont("Helvetica", 10)
    canvas.setFillColor(colors.HexColor("#666666"))
    implementation_text = "Implementation Partner: Magic Bus India Foundation"
    canvas.drawCentredString(page_width / 2.0, 1.5 * cm, implementation_text)
    
    canvas.restoreState()


def export_to_pdf(
    markdown_content: str,
    output_path: str = "output/report.pdf",
    image_dir: str = None,
    orientation: str = "portrait",
    front_page_data: Dict = None,
    output_dir: str = None,
) -> str:
    """
    Export markdown content to PDF.

    Args:
        markdown_content: Markdown string to convert
        output_path: Path where PDF should be saved
        image_dir: Optional base directory for images (for relative path resolution)
        orientation: Page orientation - "portrait" or "landscape" (default: "portrait")
        front_page_data: Optional dict with front page data:
            - report_title: str
            - report_date: str (optional)
            - funder_logo_path: str (optional)
            - funder_name: str (optional)

    Returns:
        Path to generated PDF file
    """
    print(f"Exporting to PDF: {output_path} ({orientation} mode)")

    # Determine page size based on orientation
    if orientation.lower() == "landscape":
        page_size = landscape(A4)
        is_landscape = True
    else:
        page_size = A4
        is_landscape = False

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    def find_image_path(img_path: str, base_dir: str) -> str:
        """Find image file by trying multiple path locations."""
        # Try as absolute path
        if os.path.isabs(img_path) and os.path.exists(img_path):
            return img_path

        # Try relative to base_dir
        if base_dir:
            full_path = os.path.join(base_dir, img_path)
            if os.path.exists(full_path):
                return full_path

        # Try relative to output_dir (for charts)
        if output_dir:
            full_path = os.path.join(output_dir, img_path)
            if os.path.exists(full_path):
                return full_path
            # Try with just filename in output_dir (for charts)
            filename = os.path.basename(img_path)
            full_path = os.path.join(output_dir, filename)
            if os.path.exists(full_path):
                return full_path

        # Try relative to current working directory
        if os.path.exists(img_path):
            return os.path.abspath(img_path)

        # Try relative to base_dir with normalized path
        if base_dir:
            # Normalize separators
            normalized = img_path.replace("/", os.sep).replace("\\", os.sep)
            full_path = os.path.join(base_dir, normalized)
            if os.path.exists(full_path):
                return full_path

        # Try from project root
        project_root = os.path.abspath(".")
        full_path = os.path.join(project_root, img_path)
        if os.path.exists(full_path):
            return full_path

        # Try normalized from project root
        normalized = img_path.replace("/", os.sep).replace("\\", os.sep)
        full_path = os.path.join(project_root, normalized)
        if os.path.exists(full_path):
            return full_path

        return None

    def add_header_footer(canvas, doc):
        """Add header and footer to each page."""
        canvas.saveState()

        # Get current page size (width, height)
        page_width, page_height = page_size

        # Header
        canvas.setFont("Helvetica-Bold", 10)
        canvas.setFillColor(colors.HexColor("#2c3e50"))
        canvas.drawCentredString(
            page_width / 2.0, page_height - 1.5 * cm, "Donor Report"
        )

        # Footer
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#7f8c8d"))
        page_num = canvas.getPageNumber()
        canvas.drawCentredString(page_width / 2.0, 1 * cm, f"Page {page_num}")

        # Footer line
        canvas.setStrokeColor(colors.HexColor("#bdc3c7"))
        canvas.setLineWidth(0.5)
        canvas.line(2 * cm, 1.5 * cm, page_width - 2 * cm, 1.5 * cm)

        canvas.restoreState()

    # Create front page as separate PDF if front_page_data is provided
    front_page_pdf_path = None
    print(f"Front page data check: {front_page_data is not None}")
    if front_page_data:
        print(f"Creating front page with data: {front_page_data}")
        # Create temporary front page PDF in the same directory as output
        abs_output_path = os.path.abspath(output_path)
        output_dir = os.path.dirname(abs_output_path)
        if not output_dir or output_dir == ".":
            output_dir = os.path.abspath(".")
        os.makedirs(output_dir, exist_ok=True)
        output_filename = os.path.basename(abs_output_path)
        front_page_pdf_path = os.path.join(output_dir, output_filename.replace(".pdf", "_front_page_temp.pdf"))
        print(f"Output directory: {output_dir}")
        print(f"Front page PDF will be created at: {front_page_pdf_path}")
        front_page_canvas = pdf_canvas.Canvas(front_page_pdf_path, pagesize=page_size)
        
        # Look for Magic Bus logo in common locations
        magic_bus_logo = None
        
        # Priority 1: Check assets directory in project root
        assets_dir = os.path.join(os.path.abspath("."), "assets")
        for logo_name in ["magic_bus_logo.png", "magic_bus_logo.jpg", "Magic_Bus_Logo.png", "Magic_Bus_Logo.jpg", "magicbus_logo.png", "magicbus_logo.jpg"]:
            logo_path = os.path.join(assets_dir, logo_name)
            if os.path.exists(logo_path):
                magic_bus_logo = logo_path
                print(f"Found Magic Bus logo in assets: {logo_path}")
                break
        
        # Priority 2: Check project root
        if not magic_bus_logo:
            for logo_name in ["magic_bus_logo.png", "magic_bus_logo.jpg", "Magic_Bus_Logo.png", "Magic_Bus_Logo.jpg", "magicbus_logo.png", "magicbus_logo.jpg", "logo.png", "logo.jpg"]:
                logo_path = os.path.join(os.path.abspath("."), logo_name)
                if os.path.exists(logo_path):
                    magic_bus_logo = logo_path
                    print(f"Found Magic Bus logo in root: {logo_path}")
                    break
        
        # Priority 3: Check image directory
        if not magic_bus_logo and image_dir:
            for logo_name in ["magic_bus_logo.png", "magic_bus_logo.jpg", "Magic_Bus_Logo.png", "Magic_Bus_Logo.jpg", "magicbus_logo.png", "magicbus_logo.jpg", "logo.png", "logo.jpg"]:
                logo_path = os.path.join(image_dir, logo_name)
                if os.path.exists(logo_path):
                    magic_bus_logo = logo_path
                    print(f"Found Magic Bus logo in image dir: {logo_path}")
                    break
        
        if not magic_bus_logo:
            print("Magic Bus logo not found. Using text fallback.")
        
        # Create front page on canvas
        create_front_page(
            canvas=front_page_canvas,
            doc=None,  # Not needed for manual canvas
            report_title=front_page_data.get("report_title", "Donor Report"),
            report_subtitle=front_page_data.get("report_subtitle"),
            report_date=front_page_data.get("report_date"),
            location=front_page_data.get("location"),
            report_type=front_page_data.get("report_type"),
            funder_logo_path=front_page_data.get("funder_logo_path"),
            funder_name=front_page_data.get("funder_name"),
            front_page_image_path=front_page_data.get("front_page_image_path"),
            magic_bus_logo_path=magic_bus_logo,
            page_size=page_size,
        )
        
        front_page_canvas.showPage()
        front_page_canvas.save()
        print(f"✓ Created front page PDF: {front_page_pdf_path}")
        if os.path.exists(front_page_pdf_path):
            file_size = os.path.getsize(front_page_pdf_path)
            print(f"  Front page PDF size: {file_size / 1024:.2f} KB")
        else:
            print(f"  ERROR: Front page PDF was not created!")

    try:
        # Create PDF document with header/footer
        doc = SimpleDocTemplate(
            output_path,
            pagesize=page_size,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=3 * cm,  # Increased for header
            bottomMargin=2.5 * cm,  # Increased for footer
            onFirstPage=add_header_footer,
            onLaterPages=add_header_footer,
        )

        # Get styles
        styles = getSampleStyleSheet()

        # Custom styles - Professional and clean
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=32,
            textColor=colors.HexColor("#1a1a1a"),
            spaceAfter=30,
            spaceBefore=0,
            fontName="Helvetica-Bold",
            alignment=1,  # Center
            leading=38,
        )

        heading2_style = ParagraphStyle(
            "CustomHeading2",
            parent=styles["Heading2"],
            fontSize=18,
            textColor=colors.HexColor("#ffffff"),
            spaceAfter=18,
            spaceBefore=15,
            fontName="Helvetica-Bold",
            borderWidth=1,
            borderColor=colors.HexColor("#2980b9"),
            borderPadding=10,
            backColor=colors.HexColor("#2980b9"),
            leftIndent=0,
            leading=22,
        )

        heading3_style = ParagraphStyle(
            "CustomHeading3",
            parent=styles["Heading3"],
            fontSize=14,
            textColor=colors.HexColor("#2980b9"),
            spaceAfter=14,
            spaceBefore=12,
            fontName="Helvetica-Bold",
            leading=18,
            borderWidth=0,
            borderPadding=0,
            leftIndent=0,
            borderColor=colors.HexColor("#2980b9"),
        )

        body_style = ParagraphStyle(
            "CustomBody",
            parent=styles["Normal"],
            fontSize=11,
            textColor=colors.HexColor("#2c3e50"),
            spaceAfter=10,
            leading=16,
            fontName="Helvetica",
            alignment=4,  # Justify
        )

        # Convert markdown to simple format and build story
        story = []
        base_dir = os.path.abspath(image_dir) if image_dir else os.path.abspath(".")

        # Split markdown into lines
        lines = markdown_content.split("\n")

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if not line:
                story.append(Spacer(1, 4))
                i += 1
                continue

            # Skip horizontal rules (---, ***, ___)
            if re.match(r"^[-*_]{3,}$", line):
                i += 1
                continue

            # Headings
            if line.startswith("# "):
                story.append(Spacer(1, 8))
                story.append(Paragraph(_sanitize_paragraph_markup(line[2:]), title_style))
            elif line.startswith("## "):
                story.append(Paragraph(_sanitize_paragraph_markup(line[3:]), heading2_style))
            elif line.startswith("### "):
                story.append(Paragraph(_sanitize_paragraph_markup(line[4:]), heading3_style))
            # Images
            elif line.startswith("!["):
                img_match = re.search(r"!\[(.*?)\]\((.*?)\)", line)
                if img_match:
                    img_caption = img_match.group(1)
                    img_path = img_match.group(2)

                    # Find actual image path
                    actual_path = find_image_path(img_path, base_dir)

                    if actual_path and os.path.exists(actual_path):
                        try:
                            story.append(Spacer(1, 10))

                            # Adjust image size based on orientation
                            if is_landscape:
                                # Landscape: wider images
                                max_width = 24 * cm  # Wider for landscape
                                max_height = 14 * cm
                            else:
                                # Portrait: standard A4 width
                                max_width = 16 * cm
                                max_height = 12 * cm

                            # Ensure all report images have borders.
                            actual_path = _ensure_bordered_image(actual_path, cache_dir=output_dir)

                            # Add image with proportional sizing
                            img = RLImage(
                                actual_path,
                                width=max_width,
                                height=max_height,
                                kind="proportional",  # Maintain aspect ratio
                            )
                            story.append(img)

                            # Add caption if provided
                            if img_caption and img_caption != "Image":
                                caption_style = ParagraphStyle(
                                    "Caption",
                                    parent=styles["Normal"],
                                    fontSize=9,
                                    textColor=colors.HexColor("#7f8c8d"),
                                    alignment=1,  # Center
                                    fontName="Helvetica-Oblique",
                                    spaceAfter=6,
                                )
                                story.append(Spacer(1, 4))
                                story.append(
                                    Paragraph(
                                        _sanitize_paragraph_markup(img_caption),
                                        caption_style,
                                    )
                                )

                            story.append(Spacer(1, 10))
                            print(f"✓ Added image: {os.path.basename(actual_path)}")
                        except Exception as e:
                            print(f"⚠ Error adding image {img_path}: {str(e)}")
                            story.append(
                                Paragraph(
                                    f"[Image: {img_caption or os.path.basename(img_path)}]",
                                    body_style,
                                )
                            )
                            story.append(Spacer(1, 6))
                    else:
                        print(f"⚠ Image not found: {img_path}")
                        story.append(
                            Paragraph(
                                f"[Image not found: {img_caption or os.path.basename(img_path)}]",
                                body_style,
                            )
                        )
                        story.append(Spacer(1, 6))
            # Lists
            elif line.startswith("- ") or line.startswith("* "):
                list_text = line[2:].strip()
                # Clean markdown formatting in list items
                list_text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", list_text)
                list_text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", list_text)
                list_text = _sanitize_paragraph_markup(list_text)

                list_style = ParagraphStyle(
                    "ListItem",
                    parent=body_style,
                    leftIndent=20,
                    bulletIndent=10,
                )
                story.append(Paragraph(f"• {list_text}", list_style))
                story.append(Spacer(1, 4))
            elif re.match(r"^\d+\.", line):
                # Clean markdown formatting in numbered list items
                list_text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", line)
                list_text = re.sub(r"\*(.*?)\*", r"<i>\1</i>", list_text)
                list_text = _sanitize_paragraph_markup(list_text)

                list_style = ParagraphStyle(
                    "NumberedListItem",
                    parent=body_style,
                    leftIndent=20,
                )
                story.append(Paragraph(list_text, list_style))
                story.append(Spacer(1, 4))
            # Tables (simple markdown tables)
            elif "|" in line and line.count("|") >= 2:
                # Extract table block
                table_lines = []
                j = i
                while j < len(lines) and "|" in lines[j].strip():
                    table_lines.append(lines[j].strip())
                    j += 1

                if table_lines and len(table_lines) > 1:
                    # Check if this is an image table (contains image markdown)
                    is_image_table = any("![" in tl for tl in table_lines)

                    if is_image_table:
                        # Handle image table (images side-by-side)
                        story.append(Spacer(1, 12))
                        image_row = []

                        # Extract images from first data row
                        first_data_row = table_lines[0] if table_lines else ""
                        image_matches = re.findall(
                            r"!\[(.*?)\]\((.*?)\)", first_data_row
                        )

                        for img_caption, img_path in image_matches:
                            actual_path = find_image_path(img_path, base_dir)
                            if actual_path and os.path.exists(actual_path):
                                try:
                                    actual_path = _ensure_bordered_image(actual_path, cache_dir=output_dir)
                                    # Resize images to fit side-by-side (2-3 per row)
                                    num_images = len(image_matches)
                                    # Adjust available width based on orientation
                                    if is_landscape:
                                        available_width = 24 * cm  # Wider for landscape
                                        img_height = 10 * cm
                                    else:
                                        available_width = 16 * cm
                                        img_height = 8 * cm

                                    img_width = available_width / num_images - 0.5 * cm
                                    img = RLImage(
                                        actual_path,
                                        width=img_width,
                                        height=img_height,
                                        kind="proportional",
                                    )
                                    image_row.append(img)
                                except Exception as e:
                                    print(
                                        f"⚠ Error loading image in table: {img_path}, {e}"
                                    )

                        if image_row:
                            # Create table with images
                            img_table = Table([image_row])
                            img_table.setStyle(
                                TableStyle(
                                    [
                                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ]
                                )
                            )
                            story.append(img_table)
                            story.append(Spacer(1, 12))

                        i = j
                        continue

                    # Regular data table
                    table_data = []
                    for idx, table_line in enumerate(table_lines):
                        if "|" in table_line and not re.match(
                            r"^[\|\s\:\-]+$", table_line
                        ):
                            cells = [
                                cell.strip() for cell in table_line.split("|")[1:-1]
                            ]
                            if cells:
                                table_data.append(cells)

                    if table_data:
                        # Convert to Paragraph objects for better formatting
                        formatted_data = []
                        for row_idx, row in enumerate(table_data):
                            formatted_row = []
                            for cell in row:
                                # Clean and format cell content
                                cell_text = _sanitize_paragraph_markup(
                                    re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", cell)
                                )
                                if row_idx == 0:  # Header row
                                    formatted_row.append(
                                        Paragraph(
                                            cell_text,
                                            ParagraphStyle(
                                                "TableHeader",
                                                parent=styles["Normal"],
                                                fontSize=11,
                                                fontName="Helvetica-Bold",
                                                textColor=colors.white,
                                            ),
                                        )
                                    )
                                else:
                                    formatted_row.append(
                                        Paragraph(
                                            cell_text,
                                            ParagraphStyle(
                                                "TableCell",
                                                parent=styles["Normal"],
                                                fontSize=10,
                                            ),
                                        )
                                    )
                            formatted_data.append(formatted_row)

                        table = Table(formatted_data, repeatRows=1)
                        table.setStyle(
                            TableStyle(
                                [
                                    (
                                        "BACKGROUND",
                                        (0, 0),
                                        (-1, 0),
                                        colors.HexColor("#2980b9"),
                                    ),
                                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                    ("FONTSIZE", (0, 0), (-1, 0), 11),
                                    ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
                                    ("TOPPADDING", (0, 0), (-1, 0), 10),
                                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                                    (
                                        "GRID",
                                        (0, 0),
                                        (-1, -1),
                                        1,
                                        colors.HexColor("#bdc3c7"),
                                    ),
                                    (
                                        "ROWBACKGROUNDS",
                                        (0, 1),
                                        (-1, -1),
                                        [colors.white, colors.HexColor("#f8f9fa")],
                                    ),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                                ]
                            )
                        )
                        story.append(Spacer(1, 10))
                        story.append(table)
                        story.append(Spacer(1, 12))

                    # Skip processed table lines
                    i = j
                    continue
            # Regular paragraphs
            else:
                # Clean markdown formatting
                clean_line = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", line)  # Bold
                clean_line = re.sub(r"\*(.*?)\*", r"<i>\1</i>", clean_line)  # Italic
                clean_line = _sanitize_paragraph_markup(clean_line)
                if clean_line.strip():
                    story.append(Paragraph(clean_line, body_style))
                    story.append(Spacer(1, 8))

            i += 1

        # Build PDF
        print(f"Building main content PDF: {output_path}")
        doc.build(story)
        print(f"✓ Main content PDF built: {output_path}")
        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            print(f"  Content PDF size: {file_size / 1024:.2f} KB")

        # Merge front page with content if front page was created
        if front_page_pdf_path and os.path.exists(front_page_pdf_path):
            try:
                import fitz  # PyMuPDF
                
                print(f"\n{'='*60}")
                print(f"MERGING FRONT PAGE")
                print(f"{'='*60}")
                print(f"Front page PDF: {front_page_pdf_path}")
                print(f"Content PDF: {output_path}")
                print(f"Front page exists: {os.path.exists(front_page_pdf_path)}")
                print(f"Content PDF exists: {os.path.exists(output_path)}")
                
                # Open both PDFs
                front_page_doc = fitz.open(front_page_pdf_path)
                content_doc = fitz.open(output_path)
                
                print(f"Front page has {len(front_page_doc)} pages")
                print(f"Content has {len(content_doc)} pages")
                
                # Create a new merged PDF document
                # Save to temporary file first to avoid "save to original must be incremental" error
                temp_merged_path = output_path.replace(".pdf", "_merged_temp.pdf")
                print(f"Creating merged PDF at temporary location: {temp_merged_path}")
                
                # Create new document and insert front page first
                merged_doc = fitz.open()  # Create new empty document
                # Insert front page at position 0
                merged_doc.insert_pdf(front_page_doc, start_at=0)
                # Insert content after front page (at the end)
                merged_doc.insert_pdf(content_doc)
                
                print(f"Inserted front page and content into merged document")
                print(f"Merged document has {len(merged_doc)} pages")
                
                # Save merged PDF to temporary file
                merged_doc.save(temp_merged_path)
                merged_doc.close()
                front_page_doc.close()
                content_doc.close()
                
                # Replace original content PDF with merged PDF
                print(f"Replacing original PDF with merged version...")
                if os.path.exists(output_path):
                    os.remove(output_path)
                os.rename(temp_merged_path, output_path)
                print(f"✓ Saved merged PDF to: {output_path}")
                
                # Verify merge
                final_doc = fitz.open(output_path)
                final_page_count = len(final_doc)
                print(f"✓ Merged front page with content PDF")
                print(f"  Final PDF has {final_page_count} pages")
                final_doc.close()
                
                if final_page_count > 0:
                    print(f"✓ Front page successfully merged!")
                else:
                    print(f"⚠ WARNING: Merged PDF has 0 pages!")
                
                # Clean up temporary front page file
                if os.path.exists(front_page_pdf_path):
                    os.remove(front_page_pdf_path)
                    print(f"✓ Cleaned up temporary front page file")
                print(f"{'='*60}\n")
            except Exception as e:
                import traceback
                print(f"\n{'='*60}")
                print(f"ERROR: Could not merge front page")
                print(f"{'='*60}")
                print(f"Error: {str(e)}")
                print(f"Traceback:")
                print(traceback.format_exc())
                print(f"Content PDF created without front page")
                print(f"{'='*60}\n")
        else:
            if front_page_data:
                print(f"\n{'='*60}")
                print(f"WARNING: Front page data provided but merge did not happen")
                print(f"{'='*60}")
                print(f"front_page_pdf_path: {front_page_pdf_path}")
                if front_page_pdf_path:
                    print(f"Front page PDF exists: {os.path.exists(front_page_pdf_path)}")
                    if not os.path.exists(front_page_pdf_path):
                        print(f"  Path: {front_page_pdf_path}")
                        print(f"  Directory exists: {os.path.exists(os.path.dirname(front_page_pdf_path))}")
                print(f"{'='*60}\n")

        print(f"✓ PDF exported successfully: {output_path}")

        # Check file size
        file_size = os.path.getsize(output_path)
        print(f"  File size: {file_size / 1024:.2f} KB")

        return output_path

    except Exception as e:
        raise ValueError(f"Error exporting to PDF: {str(e)}")
