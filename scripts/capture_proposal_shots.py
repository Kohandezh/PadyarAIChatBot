#!/usr/bin/env python3
"""Capture screenshots of the running Padyar app for the customer proposal.

Run with the app live on PORT (default 8011). Drops PNGs into proposal/screenshots/.
Admin pages are reached by injecting a pre-created admin_session cookie.
"""
import os
import sys
import time
from playwright.sync_api import sync_playwright

PORT = os.getenv("PORT", "8011")
BASE = f"http://127.0.0.1:{PORT}"
SESSION_TOKEN = "proposal_capture_session_token_2026"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proposal", "screenshots")
os.makedirs(OUT, exist_ok=True)

DESK = {"width": 1440, "height": 900}


def shot(page, name):
    path = os.path.join(OUT, f"{name}.png")
    page.screenshot(path=path)
    print(f"  ✓ {name}.png")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---------- PUBLIC CHAT (no auth) ----------
        ctx = browser.new_context(viewport=DESK, device_scale_factor=2, locale="fa-IR")
        page = ctx.new_page()
        print("Chat UI...")
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)  # let intro/video settle
        shot(page, "01-chat-welcome")

        # Switch to text view and ask a real question -> confident video answer
        try:
            page.click("#tab-text-btn")
            page.wait_for_timeout(600)
            page.fill("#user-input", "تا چند شماره لیزیک می‌کنید؟")
            page.wait_for_timeout(300)
            page.click("#send-btn")
            # wait for an answer bubble to appear
            page.wait_for_timeout(5000)
            shot(page, "02-chat-text-answer")
        except Exception as e:
            print("  ! text answer failed:", e)

        # Show the video answer view
        try:
            page.click("#tab-video-btn")
            page.wait_for_timeout(3500)
            shot(page, "03-chat-video-answer")
        except Exception as e:
            print("  ! video view failed:", e)

        ctx.close()

        # ---------- ADMIN LOGIN PAGE (no auth) ----------
        ctx = browser.new_context(viewport=DESK, device_scale_factor=2, locale="fa-IR")
        page = ctx.new_page()
        print("Admin login...")
        page.goto(BASE + "/secure-panel-inotex/login", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        shot(page, "04-admin-login")
        ctx.close()

        # ---------- ADMIN AUTHENTICATED ----------
        ctx = browser.new_context(viewport=DESK, device_scale_factor=2, locale="fa-IR")
        ctx.add_cookies([{
            "name": "admin_session",
            "value": SESSION_TOKEN,
            "domain": "127.0.0.1",
            "path": "/",
        }])
        page = ctx.new_page()

        admin_pages = [
            ("05-admin-dashboard", "/secure-panel-inotex"),
            ("06-admin-dataset", "/secure-panel-inotex/manage-datasets"),
            ("07-admin-questions", "/secure-panel-inotex/manage-questions"),
            ("08-admin-synonyms", "/secure-panel-inotex/synonyms"),
            ("09-admin-themes", "/secure-panel-inotex/themes"),
            ("10-admin-settings-account", "/secure-panel-inotex/settings/account"),
            ("11-admin-settings-ai", "/secure-panel-inotex/settings/ai"),
            ("12-admin-settings-backup", "/secure-panel-inotex/settings/backup"),
        ]
        for name, path in admin_pages:
            print(f"Admin {name}...")
            try:
                page.goto(BASE + path, wait_until="domcontentloaded")
                page.wait_for_timeout(2200)  # let charts/tables render
                shot(page, name)
            except Exception as e:
                print(f"  ! {name} failed:", e)

        ctx.close()
        browser.close()
    print("Done. Files in:", OUT)


if __name__ == "__main__":
    main()
