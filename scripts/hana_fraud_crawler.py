#!/usr/bin/env python3
"""
하나은행 금융사기 관련 공지·보도자료 PDF 수집기 (금사빠_어떡하지 RAG용)

수집 대상
  1) 보안센터 > 전기통신금융사기(대포통장)예방 > 공지사항
     - 목록 페이지 대신 글마다 있는 '이전글' 링크를 따라 과거로 이동합니다.
  2) 고객센터 > 하나소비자세상 > 금융생활정보가이드 (목록 페이지 index,1,list,N.jsp)

결과물 (--out 폴더)
  pdf/                 내려받은 PDF 원본
  txt/                 PDF에서 뽑은 텍스트 (pypdf가 설치된 경우)
  manifest.json        RAG 적재용 메타데이터 (기획서 JSON 형식 확장)
  skipped.json         사기와 무관하다고 판단해 건너뛴 글 목록 (검수용)

사용법
  pip install requests beautifulsoup4 pypdf
  python hana_fraud_crawler.py --since 2024-01-01 --out ./hana_pdfs

주의
  - 실행 전에 하나은행 이용약관과 robots.txt를 확인하세요. 기본 요청 간격은 1.5초입니다.
  - PDF는 대부분 금감원·금융위 보도자료를 하나은행이 다시 올린 것입니다.
    RAG의 authority는 PDF 본문의 원 기관으로 기록하고, 하나은행 URL은 mirror_url로 둡니다.
  - 사기 관련 여부와 유형은 키워드로 추정한 값이니 사람이 한 번 검수하세요.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("필요한 패키지가 없습니다: pip install requests beautifulsoup4 pypdf")

try:
    from pypdf import PdfReader  # 선택: 텍스트 추출
except ImportError:
    PdfReader = None

BASE = "https://www.kebhana.com"
PREVENT_SEED = f"{BASE}/cont/prevent/prevent01/1526222_136447.jsp"  # 2026-06-18 글 (가장 최근으로 확인된 글)
PREVENT_POST_RE = re.compile(r"/cont/prevent/prevent01/(\d+)_136447\.jsp")
GUIDE_LIST_FIRST = f"{BASE}/cont/customer/customer04/customer0408/index.jsp"
GUIDE_LIST_PAGE = f"{BASE}/cont/customer/customer04/customer0408/index,1,list,{{n}}.jsp"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (research crawler for fraud-prevention education project)",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ---- 사기 관련 판정 / 유형 추정 키워드 -------------------------------------------
FRAUD_KEYWORDS = [
    "보이스피싱", "피싱", "스미싱", "큐싱", "사기", "사칭", "소비자경보", "소비자 경보",
    "대포통장", "지급정지", "명의도용", "악성앱", "악성 앱", "리딩방", "유사수신",
    "불법사금융", "불법 사금융", "로맨스", "가상자산", "코인", "대출빙자", "개인정보 유출",
]
TYPE_RULES = [
    ("MESSENGER_PHISHING", ["메신저", "지인 사칭", "가족", "자녀", "엄마"]),
    ("SMISHING", ["스미싱", "큐싱", "문자", "URL", "악성앱", "악성 앱", "링크"]),
    ("LOAN_FRAUD", ["대출", "대환", "불법사금융", "불법 사금융", "카드깡"]),
    ("INSTITUTION_IMPERSONATION", ["검찰", "경찰", "금감원 사칭", "금융감독원 사칭", "기관 사칭", "출석요구", "수사"]),
    ("INVESTMENT_FRAUD", ["투자", "리딩방", "가상자산", "코인", "주식", "비상장", "유사수신", "로맨스"]),
    ("SECONDHAND_TRADE", ["중고", "직거래", "쇼핑몰"]),
]
AUTHORITY_RULES = [
    ("금융감독원", ["금융감독원", "금감원"]),
    ("금융위원회", ["금융위원회", "금융위"]),
    ("경찰청", ["경찰청"]),
    ("과학기술정보통신부", ["과학기술정보통신부", "과기정통부"]),
    ("한국인터넷진흥원", ["한국인터넷진흥원", "KISA"]),
]
DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")


@dataclass
class Item:
    id: str
    board: str
    title: str
    posted_at: str | None
    page_url: str
    pdf_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    is_fraud: bool = False
    fraud_type: list[str] = field(default_factory=list)
    authority: str | None = None
    files: list[dict] = field(default_factory=list)


class Crawler:
    def __init__(self, out: Path, delay: float):
        self.out = out
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        (out / "pdf").mkdir(parents=True, exist_ok=True)
        (out / "txt").mkdir(parents=True, exist_ok=True)

    def get(self, url: str) -> requests.Response | None:
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=20)
                time.sleep(self.delay)
                if r.status_code == 200:
                    return r
                print(f"  ! HTTP {r.status_code}: {url}")
                return None
            except requests.RequestException as e:
                print(f"  ! 요청 실패({attempt + 1}/3): {e}")
                time.sleep(self.delay * 2)
        return None

    @staticmethod
    def soup(r: requests.Response) -> BeautifulSoup:
        r.encoding = r.apparent_encoding or r.encoding
        return BeautifulSoup(r.text, "html.parser")

    # ---- 보안센터 공지: 상세 글 하나 파싱 -----------------------------------------
    def parse_prevent_post(self, url: str) -> tuple[Item | None, str | None]:
        r = self.get(url)
        if r is None:
            return None, None
        sp = self.soup(r)
        m = PREVENT_POST_RE.search(url)
        post_id = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:10]

        title = self._guess_title(sp)
        text = sp.get_text(" ", strip=True)
        posted = self._first_date(text)

        pdfs, imgs = self._collect_files(sp, url)
        prev_url = self._find_prev(sp, url, post_id)

        return Item(id=f"HANA-P-{post_id}", board="prevent01", title=title,
                    posted_at=posted, page_url=url, pdf_urls=pdfs, image_urls=imgs), prev_url

    def _find_prev(self, sp: BeautifulSoup, url: str, post_id: str) -> str | None:
        # 1순위: '이전글' 글자 근처의 링크
        for node in sp.find_all(string=re.compile("이전글")):
            el = node.parent
            for _ in range(4):  # 몇 단계 위까지 올라가며 링크 탐색
                if el is None:
                    break
                a = el.find("a", href=PREVENT_POST_RE)
                if a:
                    return urljoin(url, a["href"])
                el = el.parent
        # 2순위: 현재 글보다 번호가 작은 글 중 가장 큰 번호
        cur = int(post_id) if post_id.isdigit() else None
        cands = []
        for a in sp.find_all("a", href=PREVENT_POST_RE):
            mid = int(PREVENT_POST_RE.search(a["href"]).group(1))
            if cur is None or mid < cur:
                cands.append((mid, urljoin(url, a["href"])))
        return max(cands)[1] if cands else None

    # ---- 보안센터 공지 목록 페이지 (열리는 경우에만) ---------------------------------
    def collect_prevent_list(self, max_pages: int) -> list[str]:
        urls: list[str] = []
        for n in range(1, max_pages + 1):
            page = (f"{BASE}/cont/prevent/prevent01/index.jsp" if n == 1
                    else f"{BASE}/cont/prevent/prevent01/index,1,list,{n}.jsp")
            r = self.get(page)
            if r is None:
                break
            found = [urljoin(page, a["href"]) for a in self.soup(r).find_all("a", href=PREVENT_POST_RE)]
            new = [u for u in found if u not in urls]
            if not new:
                break
            urls += new
        return urls

    # ---- 금융생활정보가이드 목록 --------------------------------------------------
    def crawl_guide(self, max_pages: int) -> list[Item]:
        items: list[Item] = []
        for n in range(1, max_pages + 1):
            url = GUIDE_LIST_FIRST if n == 1 else GUIDE_LIST_PAGE.format(n=n)
            print(f"[가이드 목록] {n}쪽 {url}")
            r = self.get(url)
            if r is None:
                break
            sp = self.soup(r)
            found = 0
            for a in sp.find_all("a", href=True):
                href = urljoin(url, a["href"])
                if "/customer0408/__icsFiles/" not in href:
                    continue
                if "thum" in href.lower():
                    continue  # 썸네일 제외
                title = a.get_text(" ", strip=True) or (a.get("title") or "")
                block = a.find_parent(["li", "tr", "div"]) or a
                if not title:
                    title = block.get_text(" ", strip=True)[:120]
                posted = self._first_date(block.get_text(" ", strip=True)) or self._date_from_path(href)
                it = Item(id="HANA-G-" + hashlib.md5(href.encode()).hexdigest()[:10], board="customer0408",
                          title=title, posted_at=posted, page_url=url)
                (it.pdf_urls if href.lower().endswith(".pdf") else it.image_urls).append(href)
                items.append(it)
                found += 1
            if found == 0:
                break
        # 같은 파일이 여러 번 잡힐 수 있어 URL 기준 중복 제거
        uniq = {}
        for it in items:
            key = (it.pdf_urls or it.image_urls or [it.title])[0]
            uniq.setdefault(key, it)
        return list(uniq.values())

    # ---- 공통 유틸 ----------------------------------------------------------------
    @staticmethod
    def _guess_title(sp: BeautifulSoup) -> str:
        for sel in ["[class*=subject]", "[class*=tit]", "h3", "h4", "h2"]:
            el = sp.select_one(sel)
            if el and len(el.get_text(strip=True)) > 5:
                return el.get_text(" ", strip=True)[:200]
        return (sp.title.get_text(strip=True) if sp.title else "")[:200]

    @staticmethod
    def _first_date(text: str) -> str | None:
        m = DATE_RE.search(text)
        if not m:
            return None
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _date_from_path(url: str) -> str | None:
        m = re.search(r"/(20\d{2})/(\d{2})/(\d{2})/", url)
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

    @staticmethod
    def _collect_files(sp: BeautifulSoup, base: str) -> tuple[list[str], list[str]]:
        pdfs, imgs = [], []
        for a in sp.find_all("a", href=True):
            href = urljoin(base, a["href"])
            if href.lower().endswith(".pdf") and href not in pdfs:
                pdfs.append(href)
        for img in sp.find_all("img", src=True):
            src = urljoin(base, img["src"])
            if "__icsFiles" in src and src not in imgs:
                imgs.append(src)
        return pdfs, imgs

    # ---- 분류 / 다운로드 ------------------------------------------------------------
    @staticmethod
    def classify(it: Item, body_text: str = "") -> None:
        hay = f"{it.title} {body_text[:4000]}"
        it.is_fraud = any(k in hay for k in FRAUD_KEYWORDS)
        it.fraud_type = [code for code, kws in TYPE_RULES if any(k in hay for k in kws)]
        for name, kws in AUTHORITY_RULES:
            if any(k in body_text[:3000] for k in kws):
                it.authority = name
                break

    def download_pdf(self, url: str, item_id: str, idx: int) -> dict | None:
        r = self.get(url)
        if r is None or not r.content.startswith(b"%PDF"):
            print(f"  ! PDF 아님/실패: {url}")
            return None
        fname = f"{item_id}_{idx}.pdf"
        path = self.out / "pdf" / fname
        path.write_bytes(r.content)
        text = ""
        if PdfReader is not None:
            try:
                reader = PdfReader(str(path))
                text = "\n".join((p.extract_text() or "") for p in reader.pages)
                (self.out / "txt" / f"{item_id}_{idx}.txt").write_text(text, encoding="utf-8")
            except Exception as e:  # 암호화·스캔본 등
                print(f"  ! 텍스트 추출 실패({fname}): {e}")
        return {"path": f"pdf/{fname}", "url": url, "bytes": len(r.content),
                "sha256": hashlib.sha256(r.content).hexdigest(), "text": text}


def to_rag_record(it: Item, f: dict) -> dict:
    """기획서의 RAG JSON 형식에 맞춘 레코드. content는 원문 텍스트."""
    return {
        "id": f"{it.id}#{Path(f['path']).stem.split('_')[-1]}",
        "fraud_type": it.fraud_type,
        "title": it.title,
        "authority": it.authority or "미상(원문 확인 필요)",
        "action_type": None,             # 사람이 검수하며 채움: REPORT / REFUND_PROCEDURE / PREVENT ...
        "source_url": None,              # 원 기관(금감원 등) 원문 URL을 찾으면 채움
        "mirror_url": f["url"],          # 하나은행 재게시 URL
        "page_url": it.page_url,
        "posted_at": it.posted_at,
        "verified_at": None,
        "file": f["path"],
        "sha256": f["sha256"],
        "content": f["text"],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="하나은행 금융사기 PDF 수집기")
    ap.add_argument("--out", default="./hana_pdfs", help="저장 폴더")
    ap.add_argument("--since", default="2024-01-01", help="이 날짜 이전 글을 만나면 공지 순회 중단 (YYYY-MM-DD)")
    ap.add_argument("--seed", default=PREVENT_SEED, help="공지 순회 시작 글 URL (가장 최근 글)")
    ap.add_argument("--max-posts", type=int, default=300, help="공지 최대 순회 글 수")
    ap.add_argument("--prevent-pages", type=int, default=30, help="공지 목록 페이지 최대 쪽수")
    ap.add_argument("--extra-seed", nargs="*", default=[],
                    help="순회에서 빠진 공지 글 URL을 직접 추가 (여러 개 가능)")
    ap.add_argument("--guide-pages", type=int, default=9, help="금융생활정보가이드 최대 목록 쪽수")
    ap.add_argument("--delay", type=float, default=1.5, help="요청 간격(초)")
    ap.add_argument("--skip-guide", action="store_true", help="금융생활정보가이드 수집 안 함")
    ap.add_argument("--include-all", action="store_true", help="사기 무관 글도 PDF 다운로드")
    args = ap.parse_args()

    out = Path(args.out)
    since = datetime.strptime(args.since, "%Y-%m-%d").date()
    c = Crawler(out, args.delay)
    items: list[Item] = []

    # 1) 보안센터 공지
    #   '이전글' 연결이 중간에 끊기는 구간이 있어(예: 2025-12-15 글의 이전글이 2025-06-20 글),
    #   목록 페이지에서 글 주소를 먼저 모으고, 이전글 순회로 보충합니다.
    post_urls = c.collect_prevent_list(args.prevent_pages)
    print(f"[공지 목록] 목록 페이지에서 글 {len(post_urls)}개 확보")
    url, chain = args.seed, []
    while url and len(chain) < args.max_posts and url not in chain:
        chain.append(url)
        it, prev = c.parse_prevent_post(url)
        if it is None:
            break
        print(f"[공지] {it.posted_at} {it.title[:60]} (PDF {len(it.pdf_urls)}개)")
        if it.posted_at and date.fromisoformat(it.posted_at) < since:
            print(f"  → {args.since} 이전 글이라 순회 중단")
            break
        items.append(it)
        url = prev
    for extra in list(dict.fromkeys(post_urls + args.extra_seed)):
        if extra in chain:
            continue
        it, _ = c.parse_prevent_post(extra)
        if it and (not it.posted_at or date.fromisoformat(it.posted_at) >= since):
            print(f"[공지·보충] {it.posted_at} {it.title[:60]} (PDF {len(it.pdf_urls)}개)")
            items.append(it)

    # 2) 금융생활정보가이드
    if not args.skip_guide:
        items += [g for g in c.crawl_guide(args.guide_pages)
                  if not g.posted_at or date.fromisoformat(g.posted_at) >= since]

    # 3) 분류 + 다운로드
    records, skipped = [], []
    for it in items:
        c.classify(it)
        if not it.is_fraud and not args.include_all:
            # 제목만으로는 판단이 어려울 수 있어 PDF가 있으면 본문으로 한 번 더 확인
            if not it.pdf_urls:
                skipped.append(asdict(it))
                continue
        for i, pdf in enumerate(it.pdf_urls, 1):
            f = c.download_pdf(pdf, it.id, i)
            if f is None:
                continue
            c.classify(it, f["text"])
            if not it.is_fraud and not args.include_all:
                (out / f["path"]).unlink(missing_ok=True)
                skipped.append(asdict(it))
                break
            it.files.append({k: v for k, v in f.items() if k != "text"})
            records.append(to_rag_record(it, f))
            print(f"  ✓ {f['path']} ({it.authority or '기관 미상'}, {','.join(it.fraud_type) or '유형 미상'})")
        if it.image_urls and not it.pdf_urls and it.is_fraud:
            # 이미지형 카드뉴스는 OCR이 필요하므로 목록만 남김
            records.append({**to_rag_record(it, {"path": "", "url": it.image_urls[0], "sha256": "", "text": ""}),
                            "note": "이미지 자료: OCR 필요", "image_urls": it.image_urls})

    (out / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "skipped.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료: RAG 레코드 {len(records)}건, 건너뜀 {len(skipped)}건 → {out.resolve()}")


if __name__ == "__main__":
    main()
