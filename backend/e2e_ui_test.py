"""Full UI E2E test (no LLM) — run with the Playwright-enabled Python.

Walks the real browser flow: paste both documents, create a manual assessment,
add a requirement + evidence mapping, confirm it, verify the completion card,
toggle a supporting doc, and check the summary page.
"""
import re
import sys

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:5173"

GUIDELINE_TEXT = (
    "# E2E Grant Program\n\n"
    "Applicants must submit an annual report.\n\n"
    "A project logo is recommended.\n"
)
APPLICATION_TEXT = (
    "# E2E Project\n\n"
    "We deliver training to 200 participants every year.\n\n"
    "Attached is our annual report summary.\n"
)

SCREENSHOTS = []


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        console_errors: list[str] = []
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: console_errors.append(f"console: {m.text}")
            if m.type == "error"
            else None,
        )

        # ---- 1. Documents page ----
        page.goto(BASE, wait_until="networkidle")
        expect(page.locator(".brand")).to_contain_text("Grant Application Completeness")
        expect(page.locator("footer.disclaimer")).to_contain_text(
            "not an authoritative funding-eligibility"
        )
        shot(page, "01-documents")

        # paste guideline via UI
        form = page.locator("section.card").filter(has_text="Add a document")
        form.get_by_label("Kind").select_option("guideline")
        form.get_by_label("Title").fill("E2E Guideline")
        form.get_by_role("button", name="Paste text").click()
        form.locator("textarea").fill(GUIDELINE_TEXT)
        form.get_by_role("button", name="Add document").click()
        expect(page.locator(".card").filter(has_text="E2E Guideline").first).to_be_visible(timeout=10_000)

        # paste application via UI
        form.get_by_label("Kind").select_option("application")
        form.get_by_label("Title").fill("E2E Application")
        form.locator("textarea").fill(APPLICATION_TEXT)
        form.get_by_role("button", name="Add document").click()
        expect(page.locator(".card").filter(has_text="E2E Application").first).to_be_visible(timeout=10_000)
        shot(page, "02-documents-added")

        # ---- 2. New assessment (manual, no LLM) ----
        page.goto(f"{BASE}/new", wait_until="networkidle")
        page.locator("select").nth(0).select_option(index=1)
        page.locator("select").nth(1).select_option(index=1)
        page.get_by_role("button", name="+ Add supporting document").click()
        page.get_by_placeholder("e.g. Signed cover letter").fill("Audit report")
        page.locator("label", has_text="Run AI review").locator("input").uncheck()
        page.get_by_role("button", name="Create assessment").click()
        page.wait_for_url(re.compile(r"/assessments/\d+"), timeout=10_000)
        expect(page.locator("h2")).to_contain_text("Assessment #")
        shot(page, "03-assessment-empty")

        # ---- 3. Manual requirement ----
        page.get_by_role("button", name="+ Add requirement manually").first.click()
        page.get_by_placeholder("Code (e.g. R10)").fill("R1")
        page.get_by_placeholder("Title").fill("Annual report")
        page.get_by_placeholder("Requirement text").fill(
            "Applicants must submit an annual report."
        )
        page.get_by_role("button", name="Add", exact=True).click()
        expect(page.locator(".detail h3")).to_contain_text("Annual report", timeout=10_000)

        # ---- 4. Manual mapping via passage picker ----
        expect(page.get_by_text("Add evidence manually")).to_be_visible()
        page.locator(".passage-option").filter(has_text="annual report summary").first.click()
        expect(page.locator(".manual-mapping textarea")).to_contain_text("annual report summary")
        page.locator(".manual-mapping").get_by_role("button", name="Save mapping").click()
        expect(page.locator(".badge-status-confirmed").first).to_be_visible(timeout=10_000)
        expect(page.locator("blockquote").first).to_contain_text("annual report summary")

        # ---- 5. Confirm mapping, completion card hits 100% ----
        page.get_by_role("button", name="Confirm", exact=True).click()
        expect(page.locator(".completion-card .stat-num").first).to_have_text(
            "100%", timeout=10_000
        )
        shot(page, "04-workspace-confirmed")

        # ---- 6. Filters ----
        page.locator(".chip", has_text="Mandatory").click()
        expect(page.locator(".req-list li")).to_have_count(1)
        page.locator(".chip", has_text="All").click()

        # ---- 7. Supporting doc toggle in right rail ----
        rail = page.locator(".rail")
        rail.get_by_text("Audit report").click()
        expect(rail.locator("input[type=checkbox]").first).to_be_checked()

        # ---- 8. Claim/claims + questions empty states ----
        expect(rail.get_by_text("No claims flagged.")).to_be_visible()
        expect(rail.get_by_text("No questions generated.")).to_be_visible()

        # ---- 9. Basis override + N/A ----
        page.locator(".detail-head select").select_option("recommended")
        expect(page.locator(".detail .badge-recommended").first).to_be_visible()
        page.locator(".detail-head select").select_option("mandatory")

        # ---- 10. Summary page ----
        page.get_by_role("link", name="View summary").click()
        page.wait_for_url(re.compile(r"/summary"))
        summary = page.locator(".summary-markdown")
        expect(summary).to_contain_text("Completeness Review Summary")
        expect(summary).to_contain_text("not an authoritative funding-eligibility")
        expect(summary).to_contain_text("E2E Guideline")
        expect(summary).to_contain_text("100.0%")
        shot(page, "05-summary")

        # ---- 11. Assessments list ----
        page.goto(f"{BASE}/assessments", wait_until="networkidle")
        expect(page.locator("table.table")).to_contain_text("E2E Guideline")
        shot(page, "06-assessment-list")

        browser.close()

    real_errors = [e for e in console_errors if "favicon" not in e.lower()]
    if real_errors:
        print("CONSOLE ERRORS:")
        for e in real_errors:
            print(" ", e)
        return 1
    print("UI E2E: all steps passed, 0 console/page errors")
    print("screenshots:", ", ".join(SCREENSHOTS))
    return 0


def shot(page, name: str) -> None:
    import os
    import tempfile

    path = os.path.join(tempfile.gettempdir(), f"gaca-{name}.png")
    page.screenshot(path=path, full_page=True)
    SCREENSHOTS.append(path)


if __name__ == "__main__":
    sys.exit(main())
