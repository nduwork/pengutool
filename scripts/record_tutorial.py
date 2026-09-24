"""Re-render docs/assets/tutorial/*.gif from the landing page's tutorial player (docs/tutorial.js).

Needs Python Playwright with Chrome (`pip install playwright`) and ffmpeg. Run: python3 scripts/record_tutorial.py
Each scene plays alone via docs/index.html?record=N; the video is cropped to the player and converted with
a 128-colour palette at 10 fps, 800 px wide (about 1 MB per scene). The palette is built from the frames
plus a strip of the UI's key colours, so small swatches (the legend, state glyphs) keep their exact hue,
and without dithering, which would mix neighbouring colours on thin lines (the UI is flat colour).
"""
import pathlib
# message green, reply blue, @session orange, the four session states, the editor's blue accent
KEY_COLOURS = ["3fb950", "9ecbff", "f0883e", "73c991", "cca700", "8b949e", "f48771", "0078d4"]
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
        # recorded at 2x (a 2x viewport with the page zoomed to 2, same layout): the video's 4:2:0 chroma
        # would otherwise wash out the colour of 2-3px lines. Playwright ignores device_scale_factor here.
        ctx = browser.new_context(viewport={"width": 2 * W, "height": 2 * H}, record_video_dir=str(vdir),
                                  record_video_size={"width": 2 * W, "height": 2 * H})
        ctx.add_init_script("document.addEventListener('DOMContentLoaded', () => { document.documentElement.style.zoom = '2'; })")
        page = ctx.new_page()
        page.goto(f"{(ROOT / 'docs/index.html').as_uri()}?record={n}")
        box = page.locator("#tut").bounding_box()
        page.wait_for_function("window.__done === true", timeout=60000, polling=200)
        ctx.close()
        x, y, w, h = (int(round(v)) for v in (box["x"], box["y"], box["width"], box["height"]))
        strip = "".join(f"color=c=0x{c}:s=100x60:r=10[k{i}];" for i, c in enumerate(KEY_COLOURS))
        strip += "".join(f"[k{i}]" for i in range(len(KEY_COLOURS))) + f"hstack=inputs={len(KEY_COLOURS)}[keys];"
        vf = (f"[0:v]crop={w - w % 2}:{h - h % 2}:{x}:{y},fps=10,scale=800:-1:flags=lanczos,split[a][b];{strip}"
              "[a][keys]vstack=shortest=1,palettegen=max_colors=128:stats_mode=full[p];"
              "[b][p]paletteuse=dither=none:diff_mode=rectangle")
        gif = OUT / f"{n}-{name}.gif"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.7", "-i", str(next(vdir.glob("*.webm"))),
                        "-filter_complex", vf, "-loop", "0", str(gif)], check=True)
        print(f"{gif.relative_to(ROOT)}: {gif.stat().st_size / 1e6:.2f} MB")
    browser.close()
