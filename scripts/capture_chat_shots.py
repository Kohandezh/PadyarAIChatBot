#!/usr/bin/env python3
"""Re-capture chat screenshots 02 (clean video avatar) and 03 (Q&A thread)."""
import os
from playwright.sync_api import sync_playwright

PORT = os.getenv("PORT", "8011")
BASE = f"http://127.0.0.1:{PORT}"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proposal", "screenshots")
DESK = {"width": 1440, "height": 900}
Q = "تا چند شماره لیزیک می‌کنید؟"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport=DESK, device_scale_factor=2, locale="fa-IR")
        page = ctx.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)  # let the intro avatar video start

        # 2) Clean video avatar — hide the "play with sound" overlay button
        page.evaluate("var b=document.getElementById('unmute-btn'); if(b) b.style.display='none';")
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(OUT, "02-chat-video-answer.png"))
        print("  ✓ 02-chat-video-answer.png (avatar)")

        # Switch to text and reliably send the question.
        page.evaluate("switchTab('text')")
        page.wait_for_timeout(800)
        page.fill("#user-input", Q)
        sent = False
        for _ in range(4):
            page.click("#send-btn")
            page.wait_for_timeout(1300)
            val = page.eval_on_selector("#user-input", "el => el.value")
            if val.strip() == "":
                sent = True
                break
        print("  sent:", sent)

        # wait for the bot answer bubble, then capture the text thread
        page.wait_for_timeout(6000)
        page.evaluate("switchTab('text')")
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(OUT, "03-chat-text-answer.png"))
        print("  ✓ 03-chat-text-answer.png")

        ctx.close()
        browser.close()
    print("Done.")


if __name__ == "__main__":
    main()
