"""Скачать LGPL-сборку FFmpeg для Windows в папку ffmpeg/ (её забирает voiceover.spec).

    python tools/fetch_ffmpeg.py            # последняя релизная LGPL-сборка BtbN (win64)
    python tools/fetch_ffmpeg.py FILE.zip   # взять уже скачанный архив

Почему LGPL: сборка «gpl» содержит x264/x265 и др., и всю поставку пришлось бы
распространять на условиях GPL. LGPL-сборку можно поставлять с платной программой,
если приложить лицензию и указать, где взять исходники (это пишется в ffmpeg/SOURCE.txt).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "ffmpeg"
API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
# пример: ffmpeg-n7.1-latest-win64-lgpl-7.1.zip (без «shared» — один ffmpeg.exe без DLL)
ASSET = re.compile(r"ffmpeg-n(\d+)\.(\d+)-latest-win64-lgpl-\d+\.\d+\.zip$")


def latest_asset() -> tuple[str, str]:
    with urllib.request.urlopen(API, timeout=60) as r:
        assets = json.load(r)["assets"]
    best = None
    for a in assets:
        m = ASSET.match(a["name"])
        if m:
            ver = (int(m.group(1)), int(m.group(2)))
            if best is None or ver > best[0]:
                best = (ver, a["name"], a["browser_download_url"])
    if best is None:
        raise SystemExit("В релизе BtbN/FFmpeg-Builds не найдена LGPL-сборка win64")
    return best[1], best[2]


def install(zip_path: Path, origin: str) -> None:
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        top = names[0].split("/")[0]
        if not any(n.endswith("bin/ffmpeg.exe") for n in names):
            raise SystemExit("В архиве нет bin/ffmpeg.exe")
        if "lgpl" not in zip_path.name.lower():
            raise SystemExit("Это не LGPL-сборка (в имени архива нет «lgpl») — не используйте её")
        if DEST.exists():
            shutil.rmtree(DEST)
        DEST.mkdir()
        for n in names:
            rel = n[len(top) + 1:]
            if not rel or n.endswith("/"):
                continue
            if rel in ("bin/ffmpeg.exe", "LICENSE.txt") or rel.startswith("LICENSE"):
                out = DEST / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(z.read(n))
    ver = re.search(r"(n\d+\.\d+)", zip_path.name)
    (DEST / "SOURCE.txt").write_text(
        "FFmpeg — LGPL v2.1 or later (see LICENSE.txt).\n"
        f"Binary: {zip_path.name}\nDownloaded from: {origin}\n"
        f"FFmpeg source code: https://github.com/FFmpeg/FFmpeg/tree/{ver.group(1) if ver else 'master'}\n"
        "Build scripts (configuration and the libraries used): https://github.com/BtbN/FFmpeg-Builds\n"
        "Written offer: on request the publisher will provide the complete corresponding source code "
        "of this FFmpeg build for at least three years from the date of distribution.\n",
        encoding="utf-8")
    print("Готово:", DEST / "bin" / "ffmpeg.exe")


def main(argv: list[str]) -> None:
    if len(argv) > 1:
        p = Path(argv[1])
        install(p, p.name)
        return
    name, url = latest_asset()
    tmp = ROOT / "build" / name
    tmp.parent.mkdir(exist_ok=True)
    print("Скачивание", url)
    urllib.request.urlretrieve(url, tmp)
    install(tmp, url)


if __name__ == "__main__":
    main(sys.argv)
