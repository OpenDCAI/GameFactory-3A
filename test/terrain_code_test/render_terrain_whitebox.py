"""Render the exported GLBs with Three.js, using the same camera and lights.

Requires playwright, Pillow and ffmpeg. On Windows the installed Edge browser
is used; elsewhere run `python -m playwright install chromium` once.
"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import threading
import urllib.request

ASSET_DIR = Path(__file__).resolve().parent
ROOT = ASSET_DIR.parents[1]
NAMES = ("plains", "hills", "basin", "canyon", "walled_town", "city")


def prepare(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    for source, target in (("terrain_whitebox_viewer.html", "index.html"),
                           ("terrain_whitebox_viewer.js", "viewer.js")):
        shutil.copyfile(ASSET_DIR / source, output / target)
    base = "https://cdn.jsdelivr.net/npm/three@0.170.0/"
    files = {"LICENSE": "LICENSE", "build/three.module.js": "three.module.js",
             "examples/jsm/loaders/GLTFLoader.js": "loaders/GLTFLoader.js",
             "examples/jsm/controls/OrbitControls.js": "controls/OrbitControls.js",
             "examples/jsm/utils/BufferGeometryUtils.js": "utils/BufferGeometryUtils.js"}
    for source, target in files.items():
        path = output / "vendor" / target
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(base + source, timeout=40) as response:
            data = response.read()
        path.write_bytes(data)
    # All writer vertices are in world space. Use the union of both versions
    # even in single-version captures, so screenshots have identical framing.
    framing = {}
    for name in NAMES:
        low, high = [math.inf] * 3, [-math.inf] * 3
        for variant in ("opus", "gpt6"):
            path = output / variant / f"{name}.glb"
            if not path.is_file():
                continue
            data = path.read_bytes()
            length = struct.unpack_from("<I", data, 12)[0]
            document = json.loads(data[20:20 + length])
            for mesh in document["meshes"]:
                for primitive in mesh["primitives"]:
                    accessor = document["accessors"][primitive["attributes"]["POSITION"]]
                    low = [min(a, b) for a, b in zip(low, accessor["min"])]
                    high = [max(a, b) for a, b in zip(high, accessor["max"])]
        if all(math.isfinite(value) for value in low + high):
            framing[name] = {"min": low, "max": high}
    (output / "framing.json").write_text(json.dumps(framing, indent=2), encoding="utf-8")


def render(output: Path, names=NAMES, variants=("opus", "gpt6"), frames=120):
    from PIL import Image, ImageChops, ImageDraw, ImageStat
    from playwright.sync_api import sync_playwright

    if frames and shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required for video; use --frames 0 for screenshots")
    prepare(output)
    handler = partial(SimpleHTTPRequestHandler, directory=str(output))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None,
                                                 headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 960}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for name in names:
                for variant in variants:
                    errors.clear()
                    folder = output / variant
                    folder.mkdir(parents=True, exist_ok=True)
                    url = f"http://127.0.0.1:{server.server_port}/?scene={name}&variant={variant}&capture=1"
                    page.goto(url)
                    page.wait_for_function("document.body.dataset.ready === 'true' || document.body.dataset.error", timeout=60000)
                    error = page.locator("body").get_attribute("data-error")
                    if error or errors:
                        raise RuntimeError(f"{variant}/{name}: {error or errors}")
                    page.evaluate("window.demo.frame(0)")
                    screenshot = page.screenshot(path=str(folder / f"{name}.png"))
                    first = Image.open(io.BytesIO(screenshot)).convert("RGB")
                    crop = first.crop((160, 120, 1120, 840))
                    deviation = max(ImageStat.Stat(crop).stddev)
                    if deviation < 8:
                        raise RuntimeError(f"blank canvas: {variant}/{name}, deviation={deviation}")
                    page.evaluate("window.demo.frame(0.35)")
                    second = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
                    difference = sum(ImageStat.Stat(ImageChops.difference(first, second)).mean)
                    if difference < 1:
                        raise RuntimeError(f"camera did not move: {variant}/{name}")
                    if frames:
                        process = subprocess.Popen([
                            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "image2pipe", "-vcodec", "mjpeg", "-r", "24", "-i", "-",
                            "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(folder / f"{name}.mp4")
                        ], stdin=subprocess.PIPE)
                        try:
                            for index in range(frames):
                                page.evaluate("angle => window.demo.frame(angle)", index * 2 * math.pi / frames)
                                process.stdin.write(page.screenshot(type="jpeg", quality=90))
                        finally:
                            process.stdin.close()
                            code = process.wait(timeout=90)
                        if code:
                            raise RuntimeError(f"ffmpeg exited with {code}: {variant}/{name}")
                    report.append(dict(scene=name, variant=variant, pixel_deviation=deviation,
                                       motion_difference=difference, frames=frames, errors=list(errors)))
                    print(f"Rendered {variant}/{name}: pixels={deviation:.1f}, motion={difference:.1f}", flush=True)
            # Exercise the actual controls and portrait layout, not just the capture path.
            review_variant = "both" if len(variants) == 2 else variants[0]
            page.goto(f"http://127.0.0.1:{server.server_port}/?variant={review_variant}")
            page.wait_for_function("document.body.dataset.ready === 'true'", timeout=60000)
            page.get_by_label("Scene", exact=True).select_option("city")
            page.wait_for_function("document.body.dataset.ready === 'true'", timeout=60000)
            page.get_by_label("Scene", exact=True).select_option("plains")
            page.wait_for_function("document.body.dataset.ready === 'true'", timeout=60000)
            page.get_by_label("White clay", exact=True).check()
            page.get_by_label("Wireframe", exact=True).check()
            page.get_by_label("Wireframe", exact=True).uncheck()
            page.get_by_label("White clay", exact=True).uncheck()
            page.get_by_role("button", name="Reset view").click()
            initial = page.evaluate("window.demo.cameras")
            page.get_by_label("Rotate", exact=True).check()
            page.wait_for_function("initial => JSON.stringify(window.demo.cameras) !== JSON.stringify(initial)", arg=initial)
            page.get_by_label("Rotate", exact=True).uncheck()
            box = page.locator("canvas").first.bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.65, box["y"] + box["height"] * 0.55, steps=8)
            page.mouse.up()
            cameras = page.evaluate("window.demo.cameras")
            if cameras == initial or any(math.dist(cameras[0], camera) > 1e-6 for camera in cameras):
                raise RuntimeError("orbit controls did not move or synchronize the cameras")
            page.get_by_role("button", name="Reset view").click()
            page.screenshot(path=str(output / "desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "mobile.png"))
            if page.evaluate("document.documentElement.scrollWidth > innerWidth"):
                raise RuntimeError("mobile page overflows horizontally")
            if page.locator("canvas").count() != len(variants):
                raise RuntimeError("review must render all requested canvases")
            for canvas in page.locator("canvas").all():
                pixels = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
                if max(ImageStat.Stat(pixels).stddev) < 8:
                    raise RuntimeError("mobile canvas is blank")
            if errors:
                raise RuntimeError(f"review controls raised browser errors: {errors}")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        report_path = output / "render_report.json"
        previous = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else []
        combined = {(row["scene"], row["variant"]): row for row in previous}
        combined.update({(row["scene"], row["variant"]): row for row in report})
        report_path.write_text(json.dumps(list(combined.values()), indent=2), encoding="utf-8")
    sheet_variants = [variant for variant in ("opus", "gpt6")
                      if any((output / variant / f"{name}.png").is_file() for name in NAMES)]
    sheet_names = [name for name in NAMES
                   if all((output / variant / f"{name}.png").is_file() for variant in sheet_variants)]
    sheet = Image.new("RGB", (640 * len(sheet_variants), 500 * len(sheet_names)), "#f8faf8")
    draw = ImageDraw.Draw(sheet)
    for row, name in enumerate(sheet_names):
        for column, variant in enumerate(sheet_variants):
            shot = Image.open(output / variant / f"{name}.png").convert("RGB")
            shot.thumbnail((640, 480))
            sheet.paste(shot, (column * 640, row * 500 + 20))
            draw.text((column * 640 + 15, row * 500 + 3), f"{name} / {variant}", fill="#26302e")
    sheet.save(output / "comparison.jpg", quality=94)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "test_data/outputs/terrain_whitebox")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--scenes", nargs="+", choices=NAMES, default=list(NAMES))
    parser.add_argument("--variants", nargs="+", choices=("opus", "gpt6"), default=["opus", "gpt6"])
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if args.frames < 0:
        parser.error("--frames must be nonnegative")
    if args.prepare_only:
        prepare(args.output)
        return 0
    return render(args.output, args.scenes, args.variants, args.frames)


if __name__ == "__main__":
    raise SystemExit(main())
