#!/usr/bin/env python3
"""
금융사기 대응 PDF → RAG 청크(JSONL) 변환기  (금사빠_어떡하지 · 긴급대응 AI-07용)

입력
  - PDF 폴더 (hana_fraud_crawler.py 의 --out/pdf, 또는 직접 받은 PDF)
  - (선택) 크롤러의 manifest.json  → 원본 URL·게시일을 메타데이터로 연결

출력
  - chunks.jsonl     임베딩·벡터DB 적재용 청크 (스키마는 README.md)
  - docs.json        문서 단위 메타데이터 (검수용)
  - needs_ocr.json   텍스트가 안 나온 스캔본 목록

사용법
  pip install pypdf pdfplumber
  python parse_pdfs.py --pdf-dir ./hana_pdfs/pdf --manifest ./hana_pdfs/manifest.json --out ./rag_out

처리 순서
  1) 텍스트 추출 (pdfplumber → 실패 시 pypdf)
  2) 정제: 쪽번호, '보 도 자 료' 머리글, 담당자 이름·직통번호, 인용 안내 문구 제거,
     줄바꿈으로 끊긴 문장 이어 붙이기
  3) 메타데이터 추출: 제목, 발표 기관, 보도일, 소비자경보 번호
  4) 섹션 나누기 (Ⅰ/1/□/<...>/[...] 같은 소제목 기준)
  5) 섹션 안에서 약 700자 단위로 자르고 앞 문단 1개를 겹침
  6) 청크마다 사기 유형·피해 상황·내용 종류를 키워드로 태깅
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

import logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)  # 폰트 경고 숨김

TODAY = date.today().isoformat()

# ---------------------------------------------------------------- 태깅 규칙
# 코드는 기획서·프론트(guidesMock.ts)와 같은 값을 씁니다.
FRAUD_TYPES = {
    "MESSENGER_PHISHING": ["메신저피싱", "메신저 피싱", "지인 사칭", "가족", "자녀", "엄마", "아이 목소리"],
    "SMISHING": ["스미싱", "큐싱", "문자메시지", "URL", "링크", "악성앱", "악성 앱", "원격조종", "원격제어"],
    "LOAN_FRAUD": ["대출빙자", "대출 빙자", "대환대출", "저금리", "불법사금융", "불법 사금융", "선입금", "대부업"],
    "INSTITUTION_IMPERSONATION": ["검찰", "검사", "경찰 사칭", "수사", "금감원 직원", "기관 사칭", "출석요구", "소환장", "카드 오발급", "카드배송"],
    "INVESTMENT_FRAUD": ["투자", "리딩방", "가상자산", "코인", "비상장", "공모주", "IPO", "유사수신", "로맨스"],
    "SECONDHAND_TRADE": ["중고거래", "중고 거래", "직거래", "안전결제", "적금계좌", "금 직거래"],
}
SITUATIONS = {
    "money_sent": ["송금", "이체", "지급정지", "피해구제", "환급", "입금"],
    "account_leak": ["계좌번호", "계좌정보", "일괄지급정지", "내계좌", "어카운트인포", "payinfo"],
    "id_leak": ["신분증", "명의도용", "안심차단", "엠세이퍼", "msafer", "노출자"],
    "personal_info_leak": ["개인정보", "노출자", "유출"],
    "malicious_app": ["악성앱", "악성 앱", "원격조종", "원격제어", "앱 설치", "비행기 모드", "초기화", "백신"],
    "suspicious_contact": ["의심", "끊고", "대표번호", "확인하", "클릭하지", "누르지"],
}
# 프론트 대응 카드 코드 (guidesMock.ts CARD_CODE)
CARD_BY_SITUATION = {
    "money_sent": "TRANSFER_RESPONSE",
    "personal_info_leak": "PERSONAL_INFO_RESPONSE",
    "id_leak": "PERSONAL_INFO_RESPONSE",
    "account_leak": "PERSONAL_INFO_RESPONSE",
    "malicious_app": "MALICIOUS_APP_RESPONSE",
    "suspicious_contact": "SUSPICIOUS_LINK_RESPONSE",
}
CONTENT_KINDS = [  # 먼저 걸리는 것 하나
    ("contact", ["☎", "국번없이", "국번 없이", "1332", "112", "1394", "118", "신고센터", "콜센터"]),
    ("procedure", ["지급정지", "피해구제", "대응요령", "대처요령", "행동요령", "신청", "등록하", "조치"]),
    ("prevention", ["유의사항", "주의", "예방", "의심하", "하지 마", "금지"]),
    ("case", ["사례", "수법", "사기범", "피해자", "접근"]),
    ("stats", ["억원", "건수", "증가", "%"]),
]
AUTHORITIES = [
    ("금융감독원", ["금융감독원", "금감원"]),
    ("금융위원회", ["금융위원회", "금융위"]),
    ("경찰청", ["경찰청", "국가수사본부"]),
    ("과학기술정보통신부", ["과학기술정보통신부", "과기정통부"]),
    ("한국인터넷진흥원", ["한국인터넷진흥원"]),
    ("국무조정실", ["국무조정실"]),
]

# ---------------------------------------------------------------- 정제 규칙
DROP_LINE = [
    re.compile(r"^-\s*\d+\s*-$"),                       # - 1 -
    re.compile(r"^보\s*도\s*(참\s*고\s*)?자\s*료$"),      # 보 도 자 료
    re.compile(r"^(책임자|담당자)\s"),                    # 담당자 이름·직통번호
    re.compile(r"\(0\d{1,2}-\d{3,4}-\d{4}\)\s*$"),       # (02-3145-8130) 로 끝나는 줄
    re.compile(r"^☞?\s*본 자료를 인용하여"),
    re.compile(r"^https?://www\.fss\.or\.kr/?$"),
    re.compile(r"^담당\s*부서"),
    re.compile(r"^(보도|배포)\s*(시점)?\s*20\d{2}"),
    re.compile(r"^등급\s*주의\s*경고\s*위험$"),
]
BULLET = re.compile(r"^([□◦○▣◈▶■●•\-\*※☞➊➋➌➍➎➏➐➑➒➓❶❷❸❹❺❻❼❽❾❿①②③④⑤⑥⑦⑧⑨⑩]|\d+[.)]|[가-하][.)]|[ⅠⅡⅢⅣⅤⅥ])")
HEADING = re.compile(
    r"^([ⅠⅡⅢⅣⅤⅥ][.\s]|\d\s{1,3}\S.{0,30}$|<[^>]{2,40}>$|\[[^\]]{2,40}\]$|【[^】]{2,40}】$|붙임\s*\d)"
)
ALERT_RE = re.compile(r"소비자\s*경보\s*(20\d{2})\s*[-–]\s*(\d+)\s*호")
DATE_RE = re.compile(r"(?:보도|배포|보도시점)\D{0,6}(20\d{2})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})")


def extract_text(pdf: Path) -> str:
    text = ""
    if pdfplumber is not None:
        try:
            with pdfplumber.open(str(pdf)) as doc:
                text = "\n".join((p.extract_text() or "") for p in doc.pages)
        except Exception:
            text = ""
    if len(text.strip()) < 50 and PdfReader is not None:
        try:
            text = "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)
        except Exception:
            pass
    return text


def clean(text: str) -> list[str]:
    """정제 후 문단 리스트를 돌려준다."""
    text = text.replace(" ", " ").replace("　", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(\S)̇", r"\1", text)  # 강조 점(̇) 제거
    lines = [ln.strip() for ln in text.splitlines()]
    paras: list[str] = []
    buf = ""
    for ln in lines:
        if not ln or any(p.search(ln) for p in DROP_LINE):
            if buf:
                paras.append(buf)
                buf = ""
            continue
        starts_new = bool(BULLET.match(ln) or HEADING.match(ln))
        prev_is_heading = bool(buf and HEADING.match(buf) and len(buf) <= 45)
        if buf and not starts_new and not prev_is_heading and not buf.endswith((".", "다", "요", "!", "?", ":")):
            buf += " " + ln  # 줄바꿈으로 끊긴 문장 이어 붙이기
        else:
            if buf:
                paras.append(buf)
            buf = ln
    if buf:
        paras.append(buf)
    # 너무 짧은 조각(표 잔해 등) 정리
    return [p for p in (re.sub(r"\s+", " ", x).strip() for x in paras) if len(p) >= 4]


def doc_meta(paras: list[str], raw: str) -> dict:
    head = " ".join(paras[:12])
    title = ""
    for p in paras[:15]:  # 보도자료 제목: 앞부분에서 15~80자, 문장부호로 끝나는 첫 문단
        if 12 <= len(p) <= 90 and not p.startswith(("□", "◦", "*", "▣")) and "담당" not in p:
            title = p
            break
    m = ALERT_RE.search(raw)
    alert = f"{m.group(1)}-{m.group(2)}호" if m else None
    d = DATE_RE.search(raw)
    published = None
    if d:
        try:
            published = date(int(d.group(1)), int(d.group(2)), int(d.group(3))).isoformat()
        except ValueError:
            pass
    authority = next((name for name, kws in AUTHORITIES if any(k in raw[:1500] for k in kws)), None)
    return {"title": title or head[:60], "alert_no": alert, "published_at": published, "authority": authority}


def split_sections(paras: list[str]) -> list[tuple[str, list[str]]]:
    sections, cur, body = [], "본문", []
    for p in paras:
        if HEADING.match(p) and len(p) <= 45:
            if body:
                sections.append((cur, body))
            cur, body = p.strip("<>[]【】 "), []
        else:
            body.append(p)
    if body:
        sections.append((cur, body))
    return sections


def chunk(section_paras: list[str], size: int, overlap: int) -> list[str]:
    out, cur = [], []
    for p in section_paras:
        if cur and sum(len(x) for x in cur) + len(p) > size:
            out.append("\n".join(cur))
            cur = cur[-overlap:] if overlap else []
        cur.append(p)
    if cur:
        out.append("\n".join(cur))
    return out


def tags(text: str) -> dict:
    ft = [k for k, kws in FRAUD_TYPES.items() if any(w in text for w in kws)]
    sc = [k for k, kws in SITUATIONS.items() if any(w in text for w in kws)]
    kind = next((k for k, kws in CONTENT_KINDS if any(w in text for w in kws)), "general")
    cards = sorted({CARD_BY_SITUATION[s] for s in sc if s in CARD_BY_SITUATION})
    if "SECONDHAND_TRADE" in ft:
        cards.append("SECONDHAND_TRADE_RESPONSE")
    if "INSTITUTION_IMPERSONATION" in ft:
        cards.append("IMPERSONATION_CALL_RESPONSE")
    return {"fraud_types": ft, "situation_codes": sc, "card_codes": sorted(set(cards)), "content_kind": kind}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--manifest", help="hana_fraud_crawler.py 가 만든 manifest.json")
    ap.add_argument("--out", default="./rag_out")
    ap.add_argument("--chunk-size", type=int, default=700, help="청크 목표 길이(글자)")
    ap.add_argument("--overlap", type=int, default=1, help="겹칠 문단 수")
    ap.add_argument("--min-chars", type=int, default=60, help="이보다 짧은 청크는 버림")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    by_file = {}
    if args.manifest:
        for rec in json.loads(Path(args.manifest).read_text(encoding="utf-8")):
            if rec.get("file"):
                by_file[Path(rec["file"]).name] = rec

    chunks, docs, needs_ocr = [], [], []
    for pdf in sorted(Path(args.pdf_dir).glob("*.pdf")):
        raw = extract_text(pdf)
        if len(raw.strip()) < 50:
            needs_ocr.append({"file": pdf.name, **({"mirror_url": by_file[pdf.name]["mirror_url"]} if pdf.name in by_file else {})})
            print(f"[OCR 필요] {pdf.name}")
            continue
        paras = clean(raw)
        meta = doc_meta(paras, raw)
        m = by_file.get(pdf.name, {})
        doc_id = m.get("id") or ("DOC-" + hashlib.sha1(pdf.read_bytes()).hexdigest()[:10])
        doc = {
            "doc_id": doc_id,
            "file": pdf.name,
            "title": m.get("title") or meta["title"],
            "authority": meta["authority"] or m.get("authority"),
            "published_at": meta["published_at"] or m.get("posted_at"),
            "alert_no": meta["alert_no"],
            "source_url": m.get("source_url"),
            "mirror_url": m.get("mirror_url"),
            "source_type": "original_pdf",
            "verified_at": TODAY,
            "effective_from": None,
            **tags(" ".join(paras)),
        }
        n = 0
        for section, body in split_sections(paras):
            for text in chunk(body, args.chunk_size, args.overlap):
                if len(text) < args.min_chars:  # 제목·경보번호만 있는 조각은 버림
                    continue
                n += 1
                t = tags(text)
                chunks.append({
                    "chunk_id": f"{doc_id}#{n:03d}",
                    "doc_id": doc_id,
                    "text": text,
                    # 임베딩용: 제목·섹션을 앞에 붙여 검색 문맥을 살린다
                    "embed_text": f"[{doc['title']}] {section}\n{text}",
                    "section": section,
                    "title": doc["title"],
                    "authority": doc["authority"],
                    "published_at": doc["published_at"],
                    "alert_no": doc["alert_no"],
                    "fraud_types": t["fraud_types"] or doc["fraud_types"],
                    "situation_codes": t["situation_codes"],
                    "card_codes": t["card_codes"],
                    "content_kind": t["content_kind"],
                    "source_url": doc["source_url"],
                    "mirror_url": doc["mirror_url"],
                    "source_type": "original_pdf",
                    "verified_at": TODAY,
                    "effective_from": None,
                })
        doc["chunk_count"] = n
        docs.append(doc)
        print(f"[OK] {pdf.name}: {doc['title'][:40]} → 청크 {n}개")

    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (out / "docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "needs_ocr.json").write_text(json.dumps(needs_ocr, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n문서 {len(docs)}개, 청크 {len(chunks)}개, OCR 필요 {len(needs_ocr)}개 → {out.resolve()}")


if __name__ == "__main__":
    main()
