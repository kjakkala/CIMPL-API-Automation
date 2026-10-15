from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer


def build_dashboard_pdf(report_data: dict, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    story = []

    title = "API Automation Dashboard Report"
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 0.2 * inch))

    generated = report_data.get("generated_at", "")
    summary = report_data.get("summary", {})

    story.append(Paragraph(f"Generated: {generated}", styles["Normal"]))
    story.append(Paragraph(f"Total: {summary.get('total', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Passed: {summary.get('passed', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Failed: {summary.get('failed', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Skipped: {summary.get('skipped', 0)}", styles["Normal"]))
    story.append(Spacer(1, 0.3 * inch))

    for section in report_data.get("sections", []):
        section_name = section.get("name", "Unknown")
        story.append(Paragraph(f"Section: {section_name}", styles["Heading2"]))
        story.append(Spacer(1, 0.1 * inch))

        for result in section.get("results", []):
            story.append(
                Paragraph(
                    f"{result.get('status', '').upper()} - {result.get('title', '')}",
                    styles["Heading4"],
                )
            )
            story.append(
                Paragraph(
                    f"Operation: {result.get('operation', '')}",
                    styles["Normal"],
                )
            )
            story.append(
                Paragraph(
                    f"TestRail: {result.get('testrail_case_url', '') or result.get('testrail_case_id', '')}",
                    styles["Normal"],
                )
            )
            story.append(Preformatted(result.get("details", ""), styles["Code"]))
            story.append(Spacer(1, 0.2 * inch))

    doc = SimpleDocTemplate(str(output_path), pagesize=A4)
    doc.build(story)


def pdf_name_for_run(section: str | None) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if section and section.lower() != "all":
        safe_section = section.replace(" ", "_")
        return f"dashboard_{safe_section}_{stamp}.pdf"
    return f"dashboard_all_{stamp}.pdf"
