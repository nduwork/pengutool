"""Re-render docs/assets/tutorial/*.gif from the landing page's tutorial player (docs/tutorial.js).

Needs Python Playwright with Chrome (`pip install playwright`) and ffmpeg. Run: python3 scripts/record_tutorial.py
Each scene plays alone via docs/index.html?record=N; the video is cropped to the player and converted with
a 128-colour palette at 10 fps, 800 px wide (about 1 MB per scene).
"""
import pathlib
import shutil
import subprocess
import tempfile

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/assets/tutorial"
NAMES = ["install", "start-a-pool", "group-the-children", "brief-the-parent", "ask-the-top", "direct-line", "keep-it-healthy"]
W, H = 1132, 700

with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    OUT.mkdir(parents=True, exist_ok=True)
    for n, name in enumerate(NAMES, 1):
        vdir = pathlib.Path(tmp) / str(n)
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(vdir),
                                  record_video_size={"width": W, "height": H})
        page = ctx.new_page()
        page.goto(f"{(ROOT / 'docs/index.html').as_uri()}?record={n}")
        box = page.locator("#tut").bounding_box()
        page.wait_for_function("window.__done === true", timeout=60000, polling=200)
        ctx.close()
        x, y, w, h = (int(round(v)) for v in (box["x"], box["y"], box["width"], box["height"]))
        vf = (f"crop={w - w % 2}:{h - h % 2}:{x}:{y},fps=10,scale=800:-1:flags=lanczos,split[a][b];"
              "[a]palettegen=max_colors=128:stats_mode=full[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
        gif = OUT / f"{n}-{name}.gif"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.7", "-i", str(next(vdir.glob("*.webm"))),
                        "-vf", vf, "-loop", "0", str(gif)], check=True)
        print(f"{gif.relative_to(ROOT)}: {gif.stat().st_size / 1e6:.2f} MB")
    browser.close()
