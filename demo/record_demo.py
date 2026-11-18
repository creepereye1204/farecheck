"""시연 영상 녹화 (docs/eval.md §4: E8 업로드→신고서, 30초 이내).

  FARECHECK_DEMO=1 uvicorn app.main:app --port 8765 &
  python demo/record_demo.py --url http://127.0.0.1:8765 --out demo/
결과: demo/farecheck_demo.mp4, demo/farecheck_demo.gif
(CDP 스크린캐스트 프레임을 받아 시스템 ffmpeg로 인코딩한다.)
"""
from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default=str(ROOT / "demo"))
    ap.add_argument("--channel", default="chrome")
    args = ap.parse_args()
    out = Path(args.out)
    tmp = out / "_video"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)
        ctx = browser.new_context(viewport={"width": 400, "height": 820}, device_scale_factor=2,
                                  is_mobile=True, has_touch=True, locale="en-US")
        page = ctx.new_page()
        tmp.mkdir(parents=True, exist_ok=True)
        frames: list[tuple[float, Path]] = []
        cdp = ctx.new_cdp_session(page)

        def on_frame(ev):
            f = tmp / f"{len(frames):05d}.jpg"
            f.write_bytes(base64.b64decode(ev["data"]))
            frames.append((ev["metadata"]["timestamp"], f))
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "everyNthFrame": 1})
        page.goto(args.url)
        page.wait_for_selector("#demo-banner:not([hidden])")
        page.wait_for_timeout(1500)

        # 1. 영수증 업로드
        page.set_input_files("#image", str(ROOT / "demo" / "e8_receipt.png"))
        page.wait_for_selector("#step-confirm:not([hidden])")
        page.wait_for_timeout(600)
        page.locator("#step-confirm").scroll_into_view_if_needed()
        page.wait_for_timeout(2200)

        # 2~3. 출발/도착
        page.locator("#step-trip").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        page.locator("#origin-q").press_sequentially("명동", delay=180)
        page.locator("#origin-results li").first.wait_for()
        page.wait_for_timeout(700)
        page.locator("#origin-results li").first.tap()
        page.locator("#dest-q").press_sequentially("김포", delay=180)
        page.locator("#dest-results li").first.wait_for()
        page.wait_for_timeout(700)
        page.locator("#dest-results li").first.tap()
        page.wait_for_timeout(600)
        page.select_option("#lang", "en")
        page.locator("#judge-btn").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        page.locator("#judge-btn").tap()

        # 4. 결과 → 신고서
        page.wait_for_selector("#step-result:not([hidden])")
        page.wait_for_timeout(800)
        page.locator("#step-result").evaluate("el => el.scrollIntoView({block: 'start'})")
        page.wait_for_timeout(3500)
        page.locator("#report").evaluate("el => el.scrollIntoView({block: 'start'})")
        page.wait_for_timeout(1200)
        for _ in range(6):
            page.locator("#report").evaluate("el => el.scrollBy({top: 90, behavior: 'smooth'})")
            page.wait_for_timeout(700)
        page.locator("#copy-btn").tap()
        page.wait_for_timeout(1800)
        page.screenshot(path=str(out / "result.png"))
        page.wait_for_timeout(300)
        cdp.send("Page.stopScreencast")
        ctx.close()
        browser.close()
    # 프레임 사이 실제 시간 간격대로 이어 붙인다
    lines = []
    for i, (ts, f) in enumerate(frames):
        nxt = frames[i + 1][0] if i + 1 < len(frames) else ts + 1.0
        lines += [f"file '{f.name}'", f"duration {max(0.02, nxt - ts):.3f}"]
    lines.append(f"file '{frames[-1][1].name}'")
    (tmp / "list.txt").write_text("\n".join(lines) + "\n")
    mp4, gif = out / "farecheck_demo.mp4", out / "farecheck_demo.gif"
    scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                    "-vf", f"{scale},fps=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(mp4)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf",
                    "fps=12,scale=400:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse",
                    str(gif)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print("saved", mp4, gif, f"({len(frames)} frames)")


if __name__ == "__main__":
    main()
