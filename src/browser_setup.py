import os
import subprocess
import sys

def ensure_chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser_path = p.chromium.executable_path

    if not os.path.exists(browser_path):
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            timeout=300
        )
