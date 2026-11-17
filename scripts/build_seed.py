#!/usr/bin/env python3
"""seed_chunks.jsonl 생성기.
Claude가 2026-11-16~17에 원문을 열어 확인한 내용을 '요약' 형태로 정리한 청크입니다.
원본 PDF 파싱 결과(parse_pdfs.py)가 생기면 같은 doc_id 의 seed 청크는 지우고 원문 청크로 대체하세요.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from parse_pdfs import CARD_BY_SITUATION as CARD  # 파서와 같은 카드 코드 규칙

VERIFIED = "2026-11-17"
H = "https://image.kebhana.com/cont/download/menu/menu08/"
SF = "https://m.samsungfire.com/download/consumer/"

# (doc_id, title, authority, published_at, alert_no, source_url, mirror_url, effective_from, [(section, kind, fraud_types, situations, text), ...])
DOCS = [
 ("FSC-2026-10-ALERT", "금융권 개인정보 유출사고, 보이스피싱·스미싱 등 피해 주의 (소비자경보 카드뉴스)", "금융위원회", "2026-10-08", None,
  "https://www.bokjiro.go.kr/ssis-tbu/cms/pc/news/promotion/1310465_1118.html", None, None, [
  ("사칭 판별", "prevention", ["INSTITUTION_IMPERSONATION","LOAN_FRAUD"], ["suspicious_contact"],
   "금융당국과 금융회사는 전화나 문자로 비밀번호·인증번호를 묻지 않는다. 피해보상이나 계좌보호를 이유로 돈을 옮기라고 하지 않고, 대출 과정에서 선입금이나 앱 설치를 요구하지 않는다. 이런 요구를 받으면 사기로 본다."),
  ("사전 조치", "prevention", [], ["id_leak","personal_info_leak","account_leak"],
   "미리 해 둘 것: 여신거래·비대면 계좌개설·오픈뱅킹 안심차단 가입(금융회사 영업점, 내계좌한눈에, 은행 앱), 금융감독원 파인에서 개인정보노출자 등록, 내계좌한눈에서 내 계좌·대출 점검, 신분증 사본과 계좌 비밀번호를 휴대폰에 저장하지 않기."),
  ("의심 연락을 받았을 때", "prevention", ["SMISHING","LOAN_FRAUD"], ["suspicious_contact"],
   "개인정보 유출 확인, 피해보상, 저금리 대출을 안내하는 문자의 URL은 누르지 말고 삭제한다. 상대가 이름·소득·대출 정보를 정확히 알고 있어도 의심하고, 유출 여부나 대출 안내는 해당 금융회사 공식 홈페이지·앱·대표번호로 직접 확인한다."),
  ("피해가 의심될 때", "procedure", [], ["money_sent","malicious_app","personal_info_leak"],
   "피해가 의심되면 경찰(1394 또는 112)이나 금융회사 콜센터에 즉시 지급정지를 요청한다. 지급정지 요청 후 최대 14일 이내, 가급적 3영업일 이내에 해당 금융회사에 피해구제 신청서를 낸다. 악성앱이 의심되면 다른 전화로 신고하고 백신앱으로 점검하거나 휴대폰을 초기화한다. 개인정보노출자 등록과 금융거래 비밀번호 변경도 한다."),
 ]),
 ("FSS-2023-06-GUIDE", "알아두면 든든한 보이스피싱 대처법 (보이스피싱 예방 종합 지침서)", "금융감독원", "2023-06-23", None,
  None, H+"notice_1492552.pdf", None, [
  ("사전 예방 제도", "prevention", [], [],
   "보이스피싱 사전 예방 제도: ATM 지연인출(100만 원 이상 입금 시 30분간 인출·이체 제한, 자동 적용), 지연이체(이체 후 최소 3시간 뒤 입금, 사전 신청), 해외 IP 차단, 입금계좌 지정(지정 외 계좌는 하루 100만 원 이내), 단말기 지정, 만 65세 이상 고령자 지정인 알림. 신청·해지 방법은 금융회사마다 다르다."),
  ("피해 시 대처 1: 지급정지", "procedure", [], ["money_sent"],
   "송금 피해가 나면 내 계좌나 사기범 계좌가 있는 금융회사 콜센터, 또는 금융감독원 콜센터(1332)에 바로 신고해 지급정지를 요청한다. 가까운 경찰서에서 사건사고사실확인원을 발급받아 지급정지를 신청한 금융회사에 제출한다. 사기계좌에 잔액이 남아 있으면 금감원 피해금 환급 절차로 구제받을 수 있다. (기한은 2026년 안내 기준 최대 14일, 가급적 3영업일)"),
  ("피해 시 대처 2: 명의도용 확인", "procedure", [], ["account_leak","id_leak"],
   "계좌정보 통합관리서비스(payinfo.or.kr)에서 내 명의로 개설된 계좌와 대출을 확인하고, 모르는 계좌는 '내계좌지급정지' 메뉴로 일괄 지급정지한다. 신분증 사본을 보냈거나 출처 불명 URL을 눌렀다면 개인정보노출자 사고예방시스템(pd.fss.or.kr)에 등록한다. 등록하면 신규 계좌 개설과 신용카드 발급이 제한된다."),
  ("피해 시 대처 3: 휴대폰 명의도용", "procedure", [], ["id_leak"],
   "명의도용 방지서비스 엠세이퍼(msafer.or.kr)에서 내 명의 휴대폰 가입 현황을 조회하고, 모르는 회선은 해당 통신사에 해지와 명의도용 신고를 한다. 가입제한 서비스를 켜면 신규 개통을 막을 수 있다."),
 ]),
 ("LAW-REFUND-PROCEDURE", "전기통신금융사기 피해금 환급 절차와 적용 대상 (통신사기피해환급법)", "금융위원회(법령) / 금융회사 안내", None, None,
  "https://www.law.go.kr/lsInfoP.do?lsId=011359", "https://www.citibank.co.kr/korea/homepage/CusFconCnts0300.act", None, [
  ("환급 절차", "procedure", [], ["money_sent"],
   "통신사기피해환급법에 따른 환급 절차: ① 피해자가 송금한 계좌의 금융회사나 사기이용계좌의 금융회사에 피해구제 신청 ② 금융회사가 입금 내역 확인 후 지급정지 ③ 금융감독원에 채권소멸 공고 요청 ④ 계좌 명의인의 이의제기 없이 약 2개월이 지나면 채권소멸 ⑤ 금감원이 14일 이내 환급액 결정 ⑥ 금융회사가 피해자에게 지급. 잔액 범위에서 환급되고 피해자가 여럿이면 나누어 받으므로 전액이 보장되지 않는다."),
  ("적용 대상과 제외", "legal", ["MESSENGER_PHISHING","INSTITUTION_IMPERSONATION","LOAN_FRAUD","INVESTMENT_FRAUD","SECONDHAND_TRADE"], ["money_sent"],
   "적용 대상: 전화·문자·메신저·PC 등 전기통신으로 속여 송금·이체하게 하거나 개인정보를 알아내 이체한 경우. 기관사칭 보이스피싱, 파밍, 금융회사 사칭 대출사기, 가족·지인 사칭 메신저피싱, 재화·용역 거래가 없는 투자사기가 해당한다. 제외: 물건·서비스를 주겠다며 속인 사기(중고거래, 온라인 쇼핑, 게임 아이템, 사이버 경매), 로맨스스캠·조건만남 같은 비정상 거래, 해킹. 제외 유형은 경찰 신고(ECRM)와 민사 절차로 대응한다."),
 ]),
 ("FSC-2023-11-FACE", "직접 현금 건넨(대면편취형) 보이스피싱도 피해구제 가능 (카드뉴스)", "금융위원회", "2023-11-17", None,
  "https://www.korea.kr/multi/visualNewsView.do?newsId=148922924", None, "2023-11-17", [
  ("대면편취형 피해구제와 일괄지급정지", "procedure", ["INSTITUTION_IMPERSONATION"], ["money_sent","account_leak"],
   "2023-11-17부터 사기범을 직접 만나 현금을 건넨 대면편취형 보이스피싱도 피해구제 대상이다. 경찰이 사기범을 검거해 사기이용계좌를 확인하면 금융회사에 지급정지를 요청하고, 피해자·피해금을 특정한 뒤 채권소멸과 환급 절차가 진행된다. 피해가 났거나 우려되면 본인 명의 모든 계좌를 한 번에 지급정지할 수 있다(어카운트인포, 파인, 거래 금융회사 영업점·고객센터). 해제는 영업점 방문으로만 가능하다."),
 ]),
 ("FSS-2026-07-NONFACE", "보이스피싱 피해구제 신청, 금융회사 앱으로 비대면 제출", "금융감독원", "2026-07-01", None,
  "https://biz.sbs.co.kr/amp/article/20000319800", None, "2026-07-01", [
  ("비대면 서류 제출", "procedure", ["SECONDHAND_TRADE"], ["money_sent"],
   "2026-07-01부터 피해자와 억울하게 지급정지된 계좌의 명의인은 영업점에 가지 않고 피해계좌가 있는 금융회사 앱에서 피해구제 신청서나 이의제기 서류를 낼 수 있다. 은행·저축은행·상호금융부터 시행하고 농협·우체국은 하반기 개시. 먼저 전화나 구두로 지급정지를 요청해야 제출할 수 있다. 중고거래 사기, 몸캠피싱처럼 보이스피싱과 무관한 범죄는 제출할 수 없다. 영업점 제출도 계속 가능하다."),
 ]),
 ("FSC-2025-12-BLOCK", "안심차단 3종 서비스 (쿠팡 개인정보 유출사고 관련 보도참고자료)", "금융위원회", "2025-12-01", "2025-27호",
  None, H+"notice_1517636.pdf", None, [
  ("안심차단 3종", "procedure", [], ["id_leak","personal_info_leak","account_leak"],
   "명의도용 대출·계좌 개설을 미리 막는 안심차단 3종: 여신거래 안심차단(신용대출, 카드론, 신용카드 발급, 할부금융 등 차단, 2024-08-23 시행), 비대면 계좌개설 안심차단(대포통장 방지, 2025-03-12 시행), 오픈뱅킹 안심차단(계좌 무단 조회·이체 차단, 2025-11-14 시행). 신청은 거래 금융회사 영업점, 어카운트인포 앱, 은행 앱에서 하고, 해제는 금융회사 영업점 방문으로 한다. 해제 후 바로 거래할 수 있다."),
 ]),
 ("GOV-COUNTERSCAM", "전기통신금융사기 통합대응단 (통합신고대응센터)", "경찰청 / 정책브리핑", "2024-06-14", None,
  "https://www.korea.kr/news/policyNewsView.do?newsId=148930266", None, "2026-02-01", [
  ("신고 창구", "contact", ["MESSENGER_PHISHING","INSTITUTION_IMPERSONATION","LOAN_FRAUD","SMISHING"], ["suspicious_contact","money_sent"],
   "보이스피싱 신고는 전화 112, 인터넷 보이스피싱지킴이로 일원화돼 한 번의 신고로 사건 접수, 악성앱 차단, 지급정지까지 처리한다. 통합대응단에는 경찰청과 금감원·KISA 상담원, 통신3사 직원이 함께 근무한다(소액결제 차단, 번호도용 문자 차단). 2026-02-01부터 통합대응단 대표번호는 1394(기존 1566-1188 병행): 상담, 의심 번호·사이트 제보, 관계기관 연계용이다. 긴급한 피해는 112. 누리집 counterscam112.go.kr."),
 ]),
 ("GOV-2026-02-REPORT", "보이스피싱 근절을 위한 범정부 통합대응 리포트", "국무조정실", "2026-02-09", None,
  "https://www.korea.kr/news/policyNewsView.do?newsId=148959241", None, None, [
  ("기관별 역할", "general", [], [],
   "통합대응단은 24시간·365일 운영되며 경찰청, 과기정통부, 금융보안원, KISA, 금융위, 방미통위, 금감원이 참여한다. 대응 흐름은 신고접수 → 범행수단 차단 → 수사 → 피해구제. 범행 번호 긴급차단은 10분 안에 이뤄지고, 안드로이드폰에는 악성앱 자동 차단이 적용됐다. 전 금융권이 AI 기반 피싱 정보공유 플랫폼(ASAP)에 참여한다."),
 ]),
 ("KISA-SMISHING", "스미싱·큐싱 대응 및 상담·신고 방법", "한국인터넷진흥원", "2025-04-28", None,
  "https://www.kisa.or.kr/1020601", None, None, [
  ("스미싱·큐싱 신고", "contact", ["SMISHING"], ["malicious_app","suspicious_contact"],
   "스미싱은 택배·청첩장 같은 일상 문자로 위장해 악성앱 설치를 유도하고, 큐싱은 QR코드로 같은 일을 한다. 감염되면 전화번호·문자 등 개인정보가 빠져나가 2차 피해로 번진다. 상담·신고: 국번 없이 118, 카카오톡 '보호나라' 채널의 스미싱/큐싱 메뉴(의심 문자·URL 확인), counterscam112.go.kr 통합신고 → 스미싱 문자 신고."),
 ]),
 ("POL-ECRM", "경찰청 사이버범죄 신고시스템(ECRM) 안내", "경찰청", "2021-12-27", None,
  "https://www.korea.kr/multi/visualNewsView.do?newsId=148897221", "https://ecrm.police.go.kr", None, [
  ("사이버사기 신고 절차", "procedure", ["SECONDHAND_TRADE"], ["money_sent"],
   "중고거래·쇼핑몰 사기처럼 환급법 대상이 아닌 사이버사기는 경찰청 사이버범죄 신고시스템(ecrm.police.go.kr)에 신고한다. 절차: 온라인 접수 → 경찰서 방문 → 조사 → 종결. 신고는 피해자 본인만 할 수 있고, 대리인은 경찰서 방문이나 국민신문고를 쓴다. 환불·배송 지연 같은 민사 분쟁은 접수 대상이 아니다. 문의 182. (2021년 자료이므로 최신 절차는 ECRM에서 확인)"),
 ]),
 ("FSS-2024-15-USED", "소비자경보 2024-15호: 자유적금계좌 악용 온라인 중고거래 사기", "금융감독원·경찰청", "2024-04-01", "2024-15호",
  None, SF+"ca_d60.pdf", None, [
  ("중고거래 사기 예방", "prevention", ["SECONDHAND_TRADE"], ["money_sent","suspicious_contact"],
   "중고거래는 가급적 직접 만나 거래하거나 안전결제 서비스를 쓴다. 판매자 계좌로 직접 보내야 한다면 그 계좌가 적금계좌인지 확인하고, 적금계좌면 사기를 의심한다. 경찰은 중고사기를 특별 단속 범죄로 추가했고 은행권은 이상거래탐지(FDS)로 대금 지급정지를 추진한다."),
 ]),
 ("FSS-2026-05-GOLD", "소비자경보 2026-05호: 금 직거래 가장 보이스피싱 자금세탁", "금융감독원", "2026-02", "2026-05호",
  None, H+"notice_1523996.pdf", None, [
  ("금 직거래 자금세탁", "case", ["SECONDHAND_TRADE","INSTITUTION_IMPERSONATION"], ["money_sent"],
   "사기범이 검찰·금감원을 사칭해 피해자에게 이체를 지시하는 동시에 중고 플랫폼의 금 판매자에게 접근해 계좌번호를 받고, 피해자가 그 계좌로 돈을 보내게 한 뒤 금을 가져간다. 판매자 계좌는 피해 신고 시 입출금이 제한되고 대금 반환을 요구받을 수 있다. 거래 상대의 신원을 확인하고 개인 계좌 대신 플랫폼 결제를 쓴다."),
 ]),
 ("FSS-2026-03-LEADING", "소비자경보 2026-3호: 코스피·코스닥 열풍 속 불법 리딩방", "금융감독원", "2026-01-26", "2026-3호",
  None, H+"notice_1522607.pdf", None, [
  ("리딩방 사기 대응", "prevention", ["INVESTMENT_FRAUD"], ["suspicious_contact","money_sent"],
   "유명 증권사 직원 사칭과 딥페이크로 신뢰를 얻고, SNS 링크로 단체방에 들인 뒤 가짜 주식앱 설치를 유도한다. 초기 수익을 보여주고 추가 입금을 받은 뒤 잠적한다. 파인(fine.fss.or.kr) '제도권 금융회사 조회'로 업체를 확인하고, 직원 재직 여부는 회사 대표번호로 확인한다. 제도권 밖 업자와의 거래는 금감원 분쟁조정 대상이 아니다. 신고: fss.or.kr → 민원·신고 → 불법금융신고센터 → 사이버불법금융행위제보, 또는 수사기관."),
 ]),
 ("FSS-2025-04-COIN", "소비자경보 2025-4호: 개인정보 유출 보상금 미끼 가상자산 사기", "금융감독원", "2025-03-05", "2025-4호",
  None, SF+"ca_d87.pdf", None, [
  ("가상자산 사기 대응", "prevention", ["INVESTMENT_FRAUD","INSTITUTION_IMPERSONATION"], ["personal_info_leak","suspicious_contact"],
   "유출된 개인정보로 '손실 보상금을 코인으로 주겠다'며 접근해 금융회사·가상자산사업자 직원을 사칭하고 정부기관 위조 공문을 보여준다. 가짜 코인 지갑 사이트에 코인이 들어온 것처럼 꾸민 뒤 판매금·담보금 명목으로 거액을 요구하고, 돈이 없으면 대출을 권한다. 금융정보분석원(kofiu.go.kr)에 신고되지 않은 가상자산사업자는 가짜 거래소일 가능성이 높다. 신고: 1332(3번), 118, 112, 금감원 가상자산 불공정거래·투자사기 신고센터."),
 ]),
 ("FSS-2026-04-AIVOICE", "소비자경보 2026-4호: AI로 조작한 아이 목소리 보이스피싱", "금융감독원", "2026-02-02", "2026-4호",
  None, H+"notice_1522677.pdf", None, [
  ("AI 목소리 납치 빙자", "case", ["MESSENGER_PHISHING"], ["money_sent","suspicious_contact"],
   "AI로 만든 자녀 울음소리로 불안을 자극하고 납치를 빙자해 술값·수리비 명목의 소액(50만 원 등)을 즉시 보내라고 한다. 울음소리와 돈 요구가 함께 오면 보이스피싱으로 의심하고 전화를 끊은 뒤 자녀에게 직접 연락해 확인한다. 이미 보냈다면 즉시 지급정지를 요청한다. 신고 112, 의심 번호 제보 counterscam112.go.kr."),
 ]),
 ("FSS-2026-04-VOICE", "보이스피싱 사기범 실제 목소리 공개 (금감원·국과수)", "금융감독원", "2026-04-02", None,
  None, H+"notice_1524911.pdf", None, [
  ("검찰·수사기관 사칭 수법", "case", ["INSTITUTION_IMPERSONATION"], ["suspicious_contact"],
   "검찰·수사기관을 사칭해 대포통장 연루를 내세워 협박하고 소환장 발부로 압박한다. 약식 조사를 핑계로 혼자 있는 장소로 옮기게 하고, 자산 내역을 묻고, 가짜 법원·검찰 사이트에 접속하게 한다. 의심 전화는 끊고 기관 공식 번호로 다시 확인하며, 계좌·비밀번호·주민번호를 말하지 않는다. 신고 counterscam112.go.kr."),
 ]),
 ("FSS-2026-09-MIDEAST", "소비자경보 2026-9호: 국제 정세 불안 틈탄 보이스피싱", "금융위원회·금융감독원", "2026-03-16", "2026-9호",
  None, H+"notice_1524574.pdf", None, [
  ("정책 지원 사칭", "case", ["INSTITUTION_IMPERSONATION","LOAN_FRAUD","SMISHING"], ["suspicious_contact","money_sent"],
   "정부·금융회사를 사칭해 긴급 수출바우처, 세금·대출 지원, 가짜 정책(에너지바우처 등)을 미끼로 악성 URL이나 앱 설치를 유도하고, 상담 뒤 기존 대출 상환 명목으로 송금을 요구한다. 지원 여부는 공식 사이트나 대표번호로 직접 확인하고, 피해가 나면 즉시 지급정지를 요청한다. 신고 112."),
 ]),
 ("FSS-2026-06-FENGSHUI", "소비자경보 2026-6호: 풍수·사주 미끼 투자사기", "금융감독원", "2026-02-12", "2026-6호",
  None, H+"notice_1523999.pdf", None, [
  ("풍수·사주 투자사기", "case", ["INVESTMENT_FRAUD"], ["suspicious_contact"],
   "SNS에서 풍수·사주 상담으로 접근해 가짜 주식앱에서 소액 수익을 보여준 뒤 거액 입금을 유도하고, 출금하려 하면 수수료·세금을 핑계로 돈을 더 받아 간다. 글로벌 투자사를 사칭해 허위 상장 정보로 비상장주식을 비싸게 팔기도 한다. 제도권 금융회사는 1:1 채팅으로 투자를 권유하지 않으므로 고객센터에 직접 확인한다."),
 ]),
 ("FSS-2025-15-IPO", "소비자경보 2025-15호: 비상장주식 '상장 임박' IPO 투자사기", "금융감독원", "2025-06-18", "2025-15호",
  None, H+"notice_1508809.pdf", None, [
  ("비상장주식 사기", "case", ["INVESTMENT_FRAUD"], ["suspicious_contact"],
   "소형 금융투자회사를 사칭한 업체가 무료 종목 추천으로 신뢰를 쌓고, 상장이 안 되면 다시 사주겠다고 약속하며 비상장주식 거액 투자를 유도한 뒤 돈을 가로챈다. '상장 임박' 고수익 권유는 사기를 의심하고 DART 공시를 확인한다. 신고 1332, 112."),
 ]),
 ("FSS-2025-09-SUBSCRIBE", "소비자경보 2025-9호: 공모주 청약 대행 사기", "금융감독원", "2025-04-21", "2025-9호",
  None, H+"notice_1507628.pdf", None, [
  ("공모주 청약 대행", "case", ["INVESTMENT_FRAUD"], ["suspicious_contact"],
   "'기관투자자와 같은 배정'을 미끼로 회사 계좌로 돈을 보내게 하고, 수익 50% 배분 일임계약을 맺은 뒤 허위 배정표와 정산내역으로 재투자를 권한다. 공모주 청약 대행 계약에 응하지 않고, 투자금을 금융회사·회사 명의 계좌로 보내지 않으며, 일임재산이 내 명의 계좌에서 운용되는지 확인한다."),
 ]),
 ("FSS-2023-25-IPOFAKE", "소비자경보 2023-25호: 회사 사칭 IPO 공모주 청약 사기", "금융감독원", "2023-12-20", "2023-25호",
  None, "https://image.kebhana.com/cont/customer/customer04/customer0408/__icsFiles/afieldfile/2024/01/04/customer0408_img_240104.pdf", None, [
  ("가짜 사전청약", "case", ["INVESTMENT_FRAUD","SMISHING"], ["suspicious_contact","personal_info_leak"],
   "IPO 진행 회사 홈페이지를 흉내 낸 사이트에서 공모가보다 싼 사전청약을 권하며 개인정보와 입금을 요구한다. 공모주 사전청약은 없다. 청약은 증권신고서에 적힌 기간에 인수인(증권사)을 통해서만 하므로 DART에서 증권신고서를 확인한다."),
 ]),
 ("FSS-2025-02-IDLOAN", "소비자경보 2025-2호: 고수익 미끼로 신분증·위임장 요구하면 대출사기", "금융감독원", "2025-02-17", "2025-2호",
  None, H+"notice_1506645.pdf", None, [
  ("신분증·위임장 요구", "case", ["LOAN_FRAUD"], ["id_leak","personal_info_leak"],
   "지인 투자로 신뢰를 얻은 뒤 경매 입찰 명목으로 신분증·위임장을 받아 명의 휴대폰을 개통하고 허위 임대차계약서로 전세·신용대출을 받아 가로챈다. 투자 명목의 신분증·위임장 요구는 거부한다. 이미 넘겼다면 내계좌한눈에로 명의도용 여부를 확인하고, 개인정보노출자 등록, 엠세이퍼 조회, 여신거래 안심차단을 신청한다."),
 ]),
 ("FSS-2025-01-HOLIDAY", "설 명절 전후 불법사금융·보이스피싱 등 민생침해 금융범죄 주의", "금융감독원", "2025-01-17", None,
  None, H+"notice_1506080_01.pdf", None, [
  ("불법사금융·스미싱 대응", "procedure", ["LOAN_FRAUD","SMISHING","INVESTMENT_FRAUD"], ["malicious_app","suspicious_contact"],
   "급전 대출을 미끼로 법정 최고금리를 넘는 이자를 받고 지인 연락처·사진을 요구한 뒤 협박하는 불법추심, 택배·카드 배송 사칭 스미싱, 가상자산·비상장주식 투자사기에 주의한다. 대부업체 등록 여부를 확인하고 대출과 무관한 개인정보 요구에 응하지 않는다. 악성앱이 의심되면 휴대폰 전원을 끄거나 비행기 모드로 바꾸고, 여신거래 안심차단을 신청한다. 신고: 112, 금감원 불법사금융신고센터 1332(3번), 채무자대리인 무료지원(대한법률구조공단 132)."),
 ]),
 ("FSS-2024-37-JOBAPP", "소비자경보 2024-37호: 2030 청년 구직자 대상 신종 보이스피싱", "금융감독원", "2024-12-17", "2024-37호",
  None, H+"notice_1505479.pdf", None, [
  ("가짜 채용 악성앱", "case", ["SMISHING"], ["malicious_app","money_sent"],
   "가짜 채용공고로 구직자에게 접근해 화상면접을 핑계로 악성앱을 설치하게 하고, 휴대폰을 원격조종해 개인정보를 빼낸 뒤 송금·대출·소액결제를 실행한다. 채용담당자가 개인 메신저로 연락하거나 URL로 앱 설치를 요구하면 거부하고, 회사 대표번호는 다른 휴대폰으로 확인한다. '보안위험 자동차단' 기능을 켠다. 피해가 나면 112로 지급정지를 요청한다."),
 ]),
 ("HANA-2024-12-LEAK", "보이스피싱 등으로 개인정보가 유출된 경우 (하나은행 안내)", "하나은행", "2024-12-16", None,
  None, H+"notice_1505429.pdf", None, [
  ("개인정보 유출 시 조치", "procedure", ["SMISHING"], ["personal_info_leak","id_leak","account_leak","malicious_app"],
   "보이스피싱으로 계좌·신분증·카드 정보가 노출됐거나, 출처 불명 URL을 눌렀거나, 원격조종 앱을 설치했다면: 계좌 일괄지급정지와 비밀번호 변경, 신분증 재발급, 원격조종 앱 삭제, 개인정보노출자 등록, 명의도용 계좌·휴대폰 개설 여부 조회를 한다. 연락처: 112, 1332, 거래 은행 고객센터."),
 ]),
 ("FSS-2024-35-ELDERLY", "소비자경보 2024-35호: 고령층 범죄연루 빙자 주택담보대출 유도", "금융감독원·경찰청", "2024-12-11", "2024-35호",
  None, H+"notice_1505404.pdf", None, [
  ("카드 오발급 빙자", "case", ["INSTITUTION_IMPERSONATION","LOAN_FRAUD"], ["suspicious_contact","money_sent"],
   "카드 오발급·배송을 빙자해 범죄에 연루됐다고 믿게 한 뒤 주택담보대출을 받게 하고 그 돈을 가로챈다. 출처 불명 링크와 앱 설치를 피하고, 카드사 연락처는 여신금융협회 홈페이지에서 확인한다. 기관 직원이라는 사람의 말은 유선전화나 다른 휴대폰으로 소속 기관 대표번호에 다시 확인한다. 국가기관은 대출을 권하지 않는다. 여신거래 안심차단을 신청해 둔다."),
 ]),
 ("FSS-2025-05-LOAN", "대출빙자형 보이스피싱 소비자경보: 피해자 10명 중 4명", "금융감독원", "2025-05-21", None,
  None, "https://image.kebhana.com/cont/customer/customer04/customer0408/__icsFiles/afieldfile/2025/06/10/customer0408_img_250610.pdf", None, [
  ("대출빙자형 흐름", "case", ["LOAN_FRAUD","SMISHING"], ["malicious_app","suspicious_contact"],
   "2025년 1분기 보이스피싱 피해자의 41.9%가 대출빙자형이다. 인터넷에 허위 저금리 대출 광고를 올리고 연락처를 남기면 상담원을 가장해 카카오톡·텔레그램으로 접근한다. 대출신청서를 가장한 파일과 함께 '대출 전용 앱'이나 '보안 앱'이라며 악성앱 링크를 보낸다. 인터넷 광고로 대출을 신청할 때는 등록업체인지 확인하고, 메신저로 앱 설치를 요구하면 거절하며, 대출 승인을 위한 선입금 요구에 응하지 않는다."),
 ]),
 ("FSS-MESSENGER-LEAFLET", "지인 사칭 메신저피싱 및 정부지원 대출·채무조정 빙자 보이스피싱 주의", "금융감독원", None, None,
  None, SF+"ca_d13.pdf", None, [
  ("메신저피싱 수법", "case", ["MESSENGER_PHISHING","LOAN_FRAUD"], ["personal_info_leak","malicious_app","money_sent"],
   "자녀·친구를 사칭해 '폰이 고장나서 친구 폰으로 연락한다'며 문자나 메신저로 접근하고 개인정보와 송금을 요구한다. 탈취한 정보로 오픈뱅킹에 가입해 다른 금융사 잔액까지 빼가거나, 악성앱을 설치하게 해 휴대폰을 원격조종하기도 한다. 소상공인 저금리 대환대출·새출발기금 같은 정부지원을 빙자해 기존 대출 상환이나 앱 설치를 요구하는 수법도 함께 나타난다. 가족이라도 반드시 전화로 직접 확인한다."),
 ]),
 ("GOV-10RULES", "보이스피싱 예방 10계명", "문화체육관광부 국민소통실", "2026-10-07", None,
  "https://m.korea.kr/news/policyNewsView.do?newsId=148973090", None, None, [
  ("10계명", "prevention", ["INSTITUTION_IMPERSONATION","MESSENGER_PHISHING","LOAN_FRAUD","SMISHING"], ["suspicious_contact"],
   "의심하고, 끊고, 확인하기. 명의도용·수사 전화는 일단 끊는다. 모텔 투숙 요구는 사기다. 가족 목소리도 AI로 위조될 수 있으니 끊고 다시 건다. 다른 사람 계좌로 대출을 갚으라는 요구, 대출 승인용 선입금 요구는 사기다. 앱 삭제·설치 지시는 거부한다. 출처 불명 링크는 누르지 않는다. 법원 등기 반송 연락은 법원에 직접 확인한다. 신청하지 않은 카드 배송 연락은 끊는다. 안심차단 서비스로 미리 막는다. 의심되면 1394."),
 ]),
 ("LAW-2026-AMEND", "통신사기피해환급법 2026년 개정 요지", "금융위원회", "2026-01-15", None,
  "https://fsc.go.kr/po010101/86063", None, "2026-08-04", [
  ("2026 개정", "legal", ["INVESTMENT_FRAUD"], ["money_sent"],
   "2026-08-04 시행 개정으로 '사기관련의심계좌' 개념과 금융·통신·수사기관 간 의심정보 공유(ASAP 플랫폼) 근거가 생겼다. 이어 2026년 10월 시행 개정으로 가상자산거래소도 금융회사처럼 의심 계정을 지급정지하고 피해를 구제할 의무를 지며, 환급 대상이 가상자산까지 넓어졌다. 시행일과 세부 조문은 국가법령정보센터에서 확인한다."),
 ]),
 ("DIR-INSTITUTIONS", "금융사기 대응 기관·창구 안내 (정리본)", "정리: 금사빠 팀(공식 자료 종합)", None, None,
  None, None, None, [
  ("연락처 총정리", "contact", [], ["money_sent","account_leak","id_leak","personal_info_leak","malicious_app","suspicious_contact"],
   "긴급 피해 신고 112. 보이스피싱 상담·제보 1394(통합대응단, counterscam112.go.kr). 지급정지는 송금·입금한 금융회사 콜센터가 가장 빠르다. 금융감독원 1332(불법사금융신고센터 3번). 스미싱·악성앱 상담 KISA 118, 카카오톡 보호나라 채널. 사이버사기(중고거래 등) 신고 ecrm.police.go.kr, 문의 182. 개인정보노출자 등록 pd.fss.or.kr(파인 fine.fss.or.kr). 계좌·대출 조회와 일괄지급정지, 안심차단 신청 payinfo.or.kr(어카운트인포). 휴대폰 명의도용 조회·가입제한 msafer.or.kr. 소액결제 차단 통신사 114. 안심차단 상태 확인 credit4u.or.kr. 불법추심 채무자대리인 무료지원 대한법률구조공단 132."),
 ]),
]


def main():
    out = Path(__file__).resolve().parent.parent / "data" / "corpus" / "seed_chunks.jsonl"
    n_all = 0
    with out.open("w", encoding="utf-8") as f:
        for doc_id, title, auth, pub, alert, src, mirror, eff, chunks in DOCS:
            for i, (section, kind, ft, sc, text) in enumerate(chunks, 1):
                cards = {CARD[s] for s in sc}
                if "SECONDHAND_TRADE" in ft: cards.add("SECONDHAND_TRADE_RESPONSE")
                if "INSTITUTION_IMPERSONATION" in ft: cards.add("IMPERSONATION_CALL_RESPONSE")
                rec = {
                    "chunk_id": f"{doc_id}#{i:03d}", "doc_id": doc_id, "text": text,
                    "embed_text": f"[{title}] {section}\n{text}",
                    "section": section, "title": title, "authority": auth,
                    "published_at": pub, "alert_no": alert,
                    "fraud_types": ft, "situation_codes": sc,
                    "card_codes": sorted(cards),
                    "content_kind": kind, "source_url": src, "mirror_url": mirror,
                    "source_type": "curated_summary", "verified_at": VERIFIED, "effective_from": eff,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_all += 1
    print(f"seed 청크 {n_all}개 → {out}")


if __name__ == "__main__":
    main()
