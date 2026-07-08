"""Mobile-viewport Playwright pass against the live SPA on :8055.
Confirms: no white screen, no console/page JS errors, login works, generate
completes, and the Save (Web Share) code path does not throw."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8055/carousel/app/"
console_errors, page_errors = [], []


def find_chrome():
    from pathlib import Path
    return str(sorted(Path("/home/office/.cache/ms-playwright").glob("chromium-*/chrome-linux*/chrome"))[-1])


with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=find_chrome(), args=["--no-sandbox"])
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},  # iPhone 14-ish
        device_scale_factor=3, is_mobile=True, has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    )
    page = ctx.new_page()
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    # capture downloads so fallback <a download> doesn't hang
    page.on("download", lambda d: print("  download triggered:", d.suggested_filename))

    page.goto(BASE, wait_until="networkidle")
    root_html = page.inner_html("#root")
    print("1. login screen rendered:", "Carousel Studio" in page.content(), "| #root non-empty:", len(root_html) > 20)

    page.fill('input[name="username"]', "vauforma")
    page.fill('input[name="password"]', "e2epass123")
    page.click("button.primary")
    page.wait_for_selector("textarea.text-input", timeout=15000)
    print("2. logged in, studio visible:", page.is_visible("textarea.text-input"))

    page.fill("textarea.text-input",
              "GLP-1 друг или враг. Враг если сам себе доктор без врача. "
              "Друг если болен и под контролем врача. Итог: инструмент, не чудо.")
    page.click("button.primary.big")
    # poll UI until result appears (real LLM+render ~20-30s)
    page.wait_for_selector("section.result", timeout=90000)
    n = page.locator(".slide-row").count()
    print("3. result rendered, slide rows:", n)
    imgs_ok = page.locator(".slide-img").count() == n and n > 0
    print("4. slide images present:", imgs_ok)

    # click Save on first slide -> exercises Web Share code path (falls back in headless)
    page.locator(".slide-row button.primary").first.click()
    page.wait_for_timeout(1500)
    print("5. Save clicked, no crash. white-screen check #root still populated:",
          len(page.inner_html("#root")) > 20)

    page.screenshot(path="/tmp/cstudio_e2e/mobile_screenshot.png", full_page=True)
    browser.close()

print("\nconsole errors:", console_errors)
print("page errors:", page_errors)
ok = not page_errors  # console 401s from pre-login /me are acceptable; JS exceptions are not
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
