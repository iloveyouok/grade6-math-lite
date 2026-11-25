import os
import uuid
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright
from backend.config import settings

def _get_jinja_env() -> Environment:
    template_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "static")
    if not os.path.exists(template_dir):
        template_dir = "frontend/static"
    return Environment(loader=FileSystemLoader(template_dir))

async def render_consolidated_pdf(batch_code: str, template_data: dict) -> str:
    """
    Renders A4 Consolidated Sheets into a merged PDF.
    Launches Playwright on-demand and closes it immediately to respect the 250-350MB memory budget.
    """
    os.makedirs(settings.PDFS_DIR, exist_ok=True)
    pdf_filename = f"batch_{batch_code}.pdf"
    pdf_path = os.path.join(settings.PDFS_DIR, pdf_filename)

    # 1. Render Jinja2 Template
    env = _get_jinja_env()
    template = env.get_template("print_sheet.html")
    html_content = template.render(**template_data)

    # 2. On-demand Playwright rendering
    async with async_playwright() as p:
        # Launch lightweight chromium headless with low-memory arguments
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote"
            ]
        )
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # Set HTML content and wait for KaTeX CDN script execution
            await page.set_content(html_content, wait_until="load")
            # Wait for KaTeX to finish typesetting if present
            try:
                await page.wait_for_timeout(400)
            except Exception:
                pass

            # Generate PDF
            await page.pdf(
                path=pdf_path,
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
            )
        finally:
            await page.close()
            await context.close()
            await browser.close()

    return f"/data/pdfs/{pdf_filename}"
