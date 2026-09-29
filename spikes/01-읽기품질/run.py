"""스파이크 1 읽기 품질 — 세 경로로 본문을 뜨고 05의 판정 기준(초기값)을 그대로 적용해 measure.csv를 만든다.

실행:
  pip install trafilatura pdfplumber playwright && playwright install chromium   (처음 한 번)
  python3 run.py                 # links.txt 전체
  python3 run.py --only arxiv    # 종류 하나만
  python3 run.py --limit 3       # 앞에서 3개만 (동작 확인용)

입력:  links.txt (종류 \t URL \t 출처 \t 제목),  tab/*.html (로그인 페이지의 탭 본문, 있으면)
출력:  raw/<번호>_<경로>.txt (추출 본문),  measure.csv (기계가 잰 값),  채점.csv (사람이 채울 표, 없을 때만 생성)

05 기준 (모두 초기값. 이 스파이크가 고치려는 대상):
  4-1 나은 쪽 고르기: 깨진 글자 2% 넘는 쪽 제외 → 길이 차이 10% 이내면 서버 쪽 → 아니면 긴 쪽
  4-2 로그인 판별:    401/403 또는 로그인 주소로 이동 / 비밀번호 칸 + 짧은 본문 / 서버 본문이 탭 본문의 30% 미만 / 페이월 표시
  4-3 잘림 판정:      펼치기 버튼 잔존 / 마지막 문단이 문장 중간에서 끊김 / 페이월 덮개 (같은 사이트 비교는 책이 쌓인 뒤라 여기선 제외)
"""
import argparse, csv, json, re, sys, time, pathlib
from dataclasses import dataclass, asdict, field

HERE = pathlib.Path(__file__).parent
RAW = HERE / "raw"; RAW.mkdir(exist_ok=True)

# ---------- 판정 기준 (05 4절 초기값). 숫자는 여기만 고친다 ----------
BROKEN_MAX = 0.02        # 4-1 깨진 글자 비율 상한
LEN_TIE = 0.10           # 4-1 길이 차이 이내면 서버 쪽
LOGIN_RATIO = 0.30       # 4-2 서버 본문 < 탭 본문 × 이 값이면 로그인 의심
SHORT_BODY = 300         # 4-2 "본문이 짧음"의 글자 수
LOGIN_URL_HINT = re.compile(r"login|signin|sign-in|auth|member/|account", re.I)
PAYWALL_HINT = re.compile(r"구독|유료|멤버십|subscribe|paywall|premium", re.I)
EXPAND_HINT = re.compile(r"더보기|더 보기|read more|continue reading|계속 읽기|전체 보기", re.I)
PASSWORD_INPUT = re.compile(r'<input[^>]+type=["\']?password', re.I)

# ---------- 자료 구조 ----------
@dataclass
class Fetch:
    """한 링크를 한 경로로 뜬 결과. 실패해도 한 줄 남긴다 (스펙 5절 1항)."""
    idx: int; kind: str; url: str; route: str          # route: server | render | tab
    ok: bool = False; status: int | None = None; final_url: str = ""
    seconds: float = 0.0; error: str = ""
    text: str = ""; html: str = ""
    # 기계가 잰 값
    length: int = 0; broken_ratio: float = 0.0
    has_password_input: bool = False; has_paywall: bool = False; has_expand: bool = False
    ends_mid_sentence: bool = False; images: int = 0

# ---------- 세 경로 ----------
def fetch_server(url: str) -> Fetch:
    """서버 수집: 주소로 HTML을 받아 trafilatura로 본문만 뽑는다. PDF는 pdfplumber. arXiv abs는 HTML 버전으로 바꿔 시도."""
    raise NotImplementedError

def fetch_render(url: str) -> Fetch:
    """서버 렌더링: Playwright로 페이지를 띄워 자바스크립트까지 돌린 뒤 같은 추출."""
    raise NotImplementedError

def fetch_tab(url: str, saved_html: pathlib.Path | None) -> Fetch:
    """탭 본문: 확장이 하는 일을 흉내 낸다. tab/에 저장 파일이 있으면 그것을, 없으면 Playwright로 연 페이지 안에서 Readability.js를 돌린다."""
    raise NotImplementedError

# ---------- 측정 ----------
def measure(f: Fetch) -> Fetch:
    """길이, 깨진 글자, 4-2·4-3에 쓰는 신호를 채운다. 판정은 하지 않는다."""
    raise NotImplementedError

# ---------- 판정 (05 초기값 그대로) ----------
def judge_login(server: Fetch, tab: Fetch | None) -> tuple[bool, str]:
    """4-2. (판정, 걸린 규칙 이름)"""
    raise NotImplementedError

def judge_truncated(f: Fetch) -> tuple[bool, str]:
    """4-3. 탭 본문에만 적용."""
    raise NotImplementedError

def pick_best(cands: list[Fetch]) -> tuple[str, str]:
    """4-1. (고른 경로, 이유 한 줄). 예: ('tab', '탭 본문이 42% 길어서')"""
    raise NotImplementedError

# ---------- 입출력 ----------
def load_links(only: str | None, limit: int | None):
    rows = []
    for line in (HERE / "links.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"): continue
        kind, url, *_ = line.split("\t")
        if only and kind != only: continue
        rows.append((kind, url))
    return rows[:limit] if limit else rows

def find_saved_tab(idx: int, url: str) -> pathlib.Path | None:
    """tab/ 안에서 이 링크의 저장 파일을 찾는다. 파일명에 번호(01_...)나 도메인이 들어 있으면 된다."""
    raise NotImplementedError

def write_measure(rows: list[dict]): ...
def write_scoring_template(links): ...   # 채점.csv: 링크마다 스펙 4절 "사람이 보는 것" 열, 이미 있으면 건드리지 않음

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    links = load_links(a.only, a.limit)
    out = []
    for i, (kind, url) in enumerate(links, 1):
        routes = {}
        if kind != "로그인후보":                     # 로그인 페이지는 탭 본문만 (스펙 3절)
            routes["server"] = measure(fetch_server(url))
            routes["render"] = measure(fetch_render(url))
        routes["tab"] = measure(fetch_tab(url, find_saved_tab(i, url)))
        for r in routes.values():
            (RAW / f"{i:02d}_{r.route}.txt").write_text(r.text, encoding="utf-8")
        login, login_rule = judge_login(routes.get("server"), routes.get("tab"))
        trunc, trunc_rule = judge_truncated(routes["tab"])
        best, why = pick_best(list(routes.values()))
        for r in routes.values():
            d = {k: v for k, v in asdict(r).items() if k not in ("text", "html")}
            d.update(login=login, login_rule=login_rule, truncated=trunc, truncated_rule=trunc_rule, best=best, best_reason=why)
            out.append(d)
        time.sleep(1.0)                              # 같은 사이트에 몰아치지 않기 (05 4절)
    write_measure(out); write_scoring_template(links)

if __name__ == "__main__":
    main()
