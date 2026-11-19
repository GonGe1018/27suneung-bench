import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import requests


from .paths import CSAT_2026_EXAMS as ROOT
MIRROR = "https://www.haksi.kr/api/mock-exams/file?id=2026-11-g3-"
TRANSCRIPT = (
    "https://files-scs.pstatic.net/2026/04/19/eJoSPTMPhZ/"
    "26%EC%88%98%EB%8A%A5%EB%93%A3%EA%B8%B0%EB%8C%80%EB%B3%B8.pdf"
)
SUBJECTS = {
    "korean": "국어",
    "math": "수학",
    "english": "영어",
    "life-ethics": "생활과 윤리",
    "ethics-thought": "윤리와 사상",
    "korean-geography": "한국지리",
    "world-geography": "세계지리",
    "east-asia-history": "동아시아사",
    "world-history": "세계사",
    "economics": "경제",
    "politics-law": "정치와 법",
    "social-culture": "사회·문화",
    "physics-1": "물리학Ⅰ",
    "chemistry-1": "화학Ⅰ",
    "biology-1": "생명과학Ⅰ",
    "earth-science-1": "지구과학Ⅰ",
    "physics-2": "물리학Ⅱ",
    "chemistry-2": "화학Ⅱ",
    "biology-2": "생명과학Ⅱ",
    "earth-science-2": "지구과학Ⅱ",
}


def download(asset):
    name, url = asset
    path = ROOT / f"{name}.pdf"
    if path.exists():
        data = path.read_bytes()
    else:
        response = requests.get(url, timeout=45)
        response.raise_for_status()
        data = response.content

    if not data.startswith(b"%PDF-"):
        raise ValueError(f"{name} is not a PDF")
    with pymupdf.open(stream=data, filetype="pdf") as document:
        pages = document.page_count
        if pages == 0:
            raise ValueError(f"{name} is empty")
    if not path.exists():
        path.write_bytes(data)
    print(f"{name}: {pages} pages, {len(data):,} bytes", flush=True)
    return {
        "file": path.name,
        "url": url,
        "pages": pages,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    assets = [
        (f"{subject}-{kind}", f"{MIRROR}{subject}-{kind}")
        for subject in SUBJECTS
        for kind in ("exam", "answer")
    ]
    assets.append(("english-transcript", TRANSCRIPT))
    with ThreadPoolExecutor(max_workers=8) as pool:
        manifest = list(pool.map(download, assets))
    (ROOT / "sources.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
