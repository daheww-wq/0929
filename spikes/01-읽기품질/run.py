"""스파이크 1 읽기 품질 — 세 경로로 본문을 뜨고 05의 판정 기준(초기값)을 그대로 적용해 measure.csv를 만든다.

실행:
  pip install -r requirements.txt            (처음 한 번. 크로미움은 playwright install chromium, 또는 CHROME_PATH로 지정)
  python3 run.py                             # links.txt 전체
  python3 run.py --only arxiv                # 종류 하나만
  python3 run.py --limit 3                   # 앞에서 3개만 (동작 확인용)
  python3 run.py --routes server,tab         # 경로 골라서
  CHROME_PATH=/opt/pw-browsers/chromium python3 run.py   # 크로미움 경로가 다를 때
  HTTPS_PROXY=... 환경 변수가 있으면 Playwright도 그 프록시를 쓴다

입력:  links.txt (종류 \t URL \t 출처 \t 제목),  tab/*.html (로그인 페이지의 탭 본문, 있으면)
출력:  raw/<번호>_<경로>.txt (추출 본문),  measure.csv (기계가 잰 값),  채점.csv (사람이 채울 표, 없을 때만 생성)

05 기준 (모두 초기값. 이 스파이크가 고치려는 대상):
  4-1 나은 쪽 고르기: 깨진 글자 2% 넘는 쪽 제외 → 길이 차이 10% 이내면 서버 쪽 → 아니면 긴 쪽
  4-2 로그인 판별:    401/403 또는 로그인 주소로 이동 / 비밀번호 칸 + 짧은 본문 / 서버 본문이 탭 본문의 30% 미만 / 페이월 표시
  4-3 잘림 판정:      펼치기 버튼 잔존 / 마지막 문단이 문장 중간에서 끊김 / 페이월 덮개 (같은 사이트 비교는 책이 쌓인 뒤라 여기선 제외)
"""
import argparse, csv, io, os, re, sys, time, pathlib, traceback
from dataclasses import dataclass, asdict

import requests
import trafilatura

HERE = pathlib.Path(__file__).parent
RAW = HERE / "raw"; RAW.mkdir(exist_ok=True)
TAB = HERE / "tab"; TAB.mkdir(exist_ok=True)
CHROME = os.environ.get("CHROME_PATH")            # 없으면 playwright가 설치한 크로미움
PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
TIMEOUT = 30
READABILITY_JS = HERE / "Readability.js"        # @mozilla/readability (npm)에서 복사. 확장이 쓰는 것과 같은 라이브러리

# ---------- 판정 기준 (05 4절 초기값). 숫자는 여기만 고친다 ----------
BROKEN_MAX = 0.02        # 4-1 깨진 글자 비율 상한
LEN_TIE = 0.10           # 4-1 길이 차이 이내면 서버 쪽
LOGIN_RATIO = 0.30       # 4-2 서버 본문 < 탭 본문 × 이 값이면 로그인 의심
SHORT_BODY = 300         # 4-2 "본문이 짧음"의 글자 수
LOGIN_URL_HINT = re.compile(r"login|signin|sign-in|/auth|member/|account|sso", re.I)
PAYWALL_HINT = re.compile(r"구독하고|유료 회원|멤버십 가입|subscribe to continue|paywall|premium content|로그인 후 이용", re.I)
EXPAND_HINT = re.compile(r"더보기|더 보기|read more|continue reading|계속 읽기|전체 보기|show more", re.I)
PASSWORD_INPUT = re.compile(r'<input[^>]+type=["\']?password', re.I)
BROKEN_CHAR = re.compile(r"[�□-]")          # 대체 문자, 흰 네모, 사용자 영역
SENTENCE_END = re.compile(r"[.!?。」』」\)\]\"”’…]$|다$|요$|죠$|까$|음$|임$")

# ---------- 자료 구조 ----------
@dataclass
class Fetch:
    """한 링크를 한 경로로 뜬 결과. 실패해도 한 줄 남긴다 (스펙 5절 1항)."""
    idx: int; kind: str; url: str; route: str          # route: server | render | tab
    ok: bool = False; status: int = 0; final_url: str = ""
    seconds: float = 0.0; error: str = ""
    text: str = ""; html: str = ""
    # 기계가 잰 값
    length: int = 0; broken_ratio: float = 0.0
    has_password_input: bool = False; has_paywall: bool = False; has_expand: bool = False
    ends_mid_sentence: bool = False; images: int = 0; redirected_to_login: bool = False


def _extract_html(html: str, url: str) -> str:
    """HTML에서 본문만. trafilatura가 비면 빈 문자열."""
    return trafilatura.extract(html, url=url, include_comments=False, include_tables=True,
                               favor_recall=True) or ""


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages)


def _arxiv_html(url: str) -> str:
    """arXiv abs/pdf 주소를 HTML 버전 주소로 (05 4-6: HTML 우선). 아니면 그대로."""
    m = re.match(r"https?://arxiv\.org/(abs|pdf)/([\w.]+?)(v\d+)?(\.pdf)?$", url)
    return f"https://arxiv.org/html/{m.group(2)}{m.group(3) or ''}" if m else url


# ---------- 세 경로 ----------
def fetch_server(idx, kind, url) -> Fetch:
    """서버 수집: 주소로 HTML을 받아 trafilatura로 본문만 뽑는다. PDF는 pypdf. arXiv는 HTML 버전 → ar5iv → 원래 주소."""
    f = Fetch(idx, kind, url, "server"); t0 = time.time()
    tries = [url]
    if "arxiv.org" in url and _arxiv_html(url) != url:
        h = _arxiv_html(url); tries = [h, h.replace("arxiv.org/html", "ar5iv.labs.arxiv.org/html"), url]
    try:
        for u in tries:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
            f.status, f.final_url = r.status_code, r.url
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ("pdf" in ctype or u.lower().endswith(".pdf")):
                f.text = _extract_pdf(r.content); f.ok = True; break
            if r.status_code == 200:
                f.html = r.text; f.text = _extract_html(r.text, u); f.ok = True; break
        if not f.ok: f.error = f"HTTP {f.status}"
    except Exception as e:
        f.error = f"{type(e).__name__}: {str(e)[:120]}"
    f.seconds = round(time.time() - t0, 2)
    return f


def _browser(p):
    kw = {"args": ["--no-sandbox"]}
    if CHROME: kw["executable_path"] = CHROME
    if PROXY: kw["proxy"] = {"server": PROXY}
    return p.chromium.launch(**kw)


def fetch_render(idx, kind, url) -> Fetch:
    """서버 렌더링: Playwright로 페이지를 띄워 자바스크립트까지 돌린 뒤 같은 추출."""
    f = Fetch(idx, kind, url, "render"); t0 = time.time()
    if url.lower().endswith(".pdf"):
        f.error = "PDF는 렌더링 경로 없음"; f.seconds = round(time.time() - t0, 2); return f
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = _browser(p); ctx = b.new_context(user_agent=UA, ignore_https_errors=True); pg = ctx.new_page()
            r = pg.goto(url, timeout=TIMEOUT * 1000, wait_until="networkidle")
            pg.wait_for_timeout(1500)
            f.status, f.final_url = (r.status if r else 0), pg.url
            f.html = pg.content(); f.text = _extract_html(f.html, pg.url); f.ok = True
            b.close()
    except Exception as e:
        f.error = f"{type(e).__name__}: {str(e)[:120]}"
    f.seconds = round(time.time() - t0, 2)
    return f


def fetch_tab(idx, kind, url, saved: pathlib.Path | None) -> Fetch:
    """탭 본문: 확장이 하는 일을 흉내 낸다. tab/에 저장 파일이 있으면 그것을, 없으면 Playwright로 연 페이지 안에서 Readability.js를 돌린다."""
    f = Fetch(idx, kind, url, "tab"); t0 = time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = _browser(p); ctx = b.new_context(user_agent=UA, ignore_https_errors=True); pg = ctx.new_page()
            if saved:
                pg.goto(saved.resolve().as_uri(), wait_until="load"); f.status, f.final_url = 200, f"file:{saved.name}"
            elif url.lower().endswith(".pdf"):
                f.error = "PDF는 탭 본문 없음 (브라우저 PDF 뷰어)"; b.close(); f.seconds = round(time.time() - t0, 2); return f
            else:
                r = pg.goto(url, timeout=TIMEOUT * 1000, wait_until="networkidle"); pg.wait_for_timeout(1500)
                f.status, f.final_url = (r.status if r else 0), pg.url
            f.html = pg.content()
            pg.add_script_tag(content=READABILITY_JS.read_text(encoding="utf-8"))
            art = pg.evaluate("() => { const a = new Readability(document.cloneNode(true)).parse(); return a ? {t: a.textContent, n: (a.content.match(/<img/g)||[]).length} : null }")
            if art: f.text = re.sub(r"\n{3,}", "\n\n", art["t"]).strip(); f.images = art["n"]
            f.ok = True
            b.close()
    except Exception as e:
        f.error = f"{type(e).__name__}: {str(e)[:120]}"
    f.seconds = round(time.time() - t0, 2)
    return f


# ---------- 측정 ----------
def measure(f: Fetch) -> Fetch:
    """길이, 깨진 글자, 4-2·4-3에 쓰는 신호를 채운다. 판정은 하지 않는다."""
    t = f.text or ""
    f.length = len(t)
    f.broken_ratio = round(len(BROKEN_CHAR.findall(t)) / f.length, 4) if f.length else 0.0
    src = f.html or ""
    f.has_password_input = bool(PASSWORD_INPUT.search(src))
    f.has_paywall = bool(PAYWALL_HINT.search(src)) or bool(PAYWALL_HINT.search(t))
    f.has_expand = bool(EXPAND_HINT.search(src[-20000:] if src else t[-2000:]))
    f.redirected_to_login = bool(LOGIN_URL_HINT.search(f.final_url or "")) and f.final_url.split("?")[0] != f.url.split("?")[0]
    last = t.rstrip().splitlines()[-1].strip() if t.strip() else ""
    f.ends_mid_sentence = bool(last) and not SENTENCE_END.search(last)
    if not f.images and src: f.images = len(re.findall(r"<img\b", src))
    return f


# ---------- 판정 (05 초기값 그대로) ----------
def judge_login(server: Fetch | None, tab: Fetch | None) -> tuple[bool, str]:
    """4-2. (판정, 걸린 규칙 이름). 서버 결과가 없으면 탭만 본다."""
    s = server
    if s:
        if s.status in (401, 403): return True, "401/403"
        if s.redirected_to_login: return True, "로그인 주소로 이동"
        if s.has_password_input and s.length < SHORT_BODY: return True, "비밀번호 칸 + 짧은 본문"
        if tab and tab.ok and tab.length and s.length < tab.length * LOGIN_RATIO: return True, f"서버 본문이 탭의 {LOGIN_RATIO:.0%} 미만"
        if s.has_paywall: return True, "페이월 표시"
    elif tab and tab.ok:
        if tab.has_password_input and tab.length < SHORT_BODY: return True, "비밀번호 칸 + 짧은 본문 (탭)"
        if tab.has_paywall: return True, "페이월 표시 (탭)"
    return False, ""


def judge_truncated(f: Fetch | None) -> tuple[bool, str]:
    """4-3. 탭 본문에만."""
    if not f or not f.ok or not f.length: return False, ""
    if f.has_expand: return True, "펼치기 버튼 잔존"
    if f.ends_mid_sentence: return True, "마지막 문단이 문장 중간에서 끊김"
    if f.has_paywall: return True, "페이월 덮개"
    return False, ""


def pick_best(cands: list[Fetch]) -> tuple[str, str]:
    """4-1. (고른 경로, 이유 한 줄). 예: ('tab', '탭 본문이 42% 길어서'). 렌더링은 서버 쪽으로 친다."""
    ok = [c for c in cands if c.ok and c.length]
    if not ok: return "", "모두 실패"
    if len(ok) == 1: return ok[0].route, "하나만 성공"
    clean = [c for c in ok if c.broken_ratio <= BROKEN_MAX] or ok
    dropped = [c.route for c in ok if c not in clean]
    note = f" (깨진 글자로 제외: {','.join(dropped)})" if dropped else ""
    server_side = [c for c in clean if c.route in ("server", "render")]
    tab = next((c for c in clean if c.route == "tab"), None)
    s = max(server_side, key=lambda c: c.length) if server_side else None
    if s and tab:
        diff = (tab.length - s.length) / max(s.length, 1)
        if abs(diff) <= LEN_TIE: return s.route, f"길이 차이 {diff:+.0%}라 서버 쪽" + note
        return (tab.route, f"탭 본문이 {diff:.0%} 길어서" + note) if diff > 0 else (s.route, f"서버 쪽이 {-diff:.0%} 길어서" + note)
    best = max(clean, key=lambda c: c.length)
    return best.route, "남은 것 중 가장 길어서" + note


# ---------- 입출력 ----------
def load_links(only, limit):
    rows = []
    for line in (HERE / "links.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"): continue
        kind, url, *rest = line.split("\t")
        if only and kind != only: continue
        rows.append((kind, url, rest[1] if len(rest) > 1 else ""))
    return rows[:limit] if limit else rows


def find_saved_tab(idx: int, url: str) -> pathlib.Path | None:
    """tab/ 안에서 이 링크의 저장 파일을 찾는다. 파일명이 번호(01_...)로 시작하거나 도메인을 담고 있으면 된다."""
    host = re.sub(r"^www\.", "", url.split("/")[2])
    for p in sorted(TAB.glob("*.htm*")):
        if p.name.startswith(f"{idx:02d}_") or host in p.name: return p
    return None


MEASURE_COLS = ["idx", "kind", "url", "route", "ok", "status", "final_url", "seconds", "error", "length", "broken_ratio",
                "has_password_input", "has_paywall", "has_expand", "ends_mid_sentence", "images", "redirected_to_login",
                "login", "login_rule", "truncated", "truncated_rule", "best", "best_reason"]


def write_measure(rows):
    with open(HERE / "measure.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=MEASURE_COLS, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def write_scoring_template(links):
    """채점.csv: 링크마다 스펙 4절 "사람이 보는 것" 열. 이미 있으면 건드리지 않는다."""
    p = HERE / "채점.csv"
    if p.exists(): return
    cols = ["idx", "kind", "제목", "url", "가장 나은 경로(server/render/tab)", "본문 누락(없음/일부/많이)", "문장 순서(정상/섞임)",
            "본문 외 혼입 개수", "혼입 위치(앞뒤/중간)", "깨진 글자(없음/있음)", "그림·표 보존(됨/일부/안됨)",
            "읽을 만한가(그대로/거슬림/못읽음)", "로그인 페이지인가(예/아니오)", "잘렸나(예/아니오)", "메모"]
    with open(p, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp); w.writerow(cols)
        for i, (kind, url, title) in enumerate(links, 1): w.writerow([i, kind, title, url] + [""] * (len(cols) - 4))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--limit", type=int)
    ap.add_argument("--routes", default="server,render,tab"); ap.add_argument("--start", type=int, default=1)
    a = ap.parse_args()
    routes_on = set(a.routes.split(","))
    links = load_links(a.only, a.limit)
    out = []
    for i, (kind, url, title) in enumerate(links, 1):
        if i < a.start: continue
        print(f"[{i:02d}/{len(links)}] {kind} {url[:70]}", flush=True)
        routes = {}
        if kind != "로그인후보":                     # 로그인 페이지는 탭 본문만 (스펙 3절)
            if "server" in routes_on: routes["server"] = measure(fetch_server(i, kind, url))
            if "render" in routes_on: routes["render"] = measure(fetch_render(i, kind, url))
        if "tab" in routes_on: routes["tab"] = measure(fetch_tab(i, kind, url, find_saved_tab(i, url)))
        for r in routes.values():
            (RAW / f"{i:02d}_{r.route}.txt").write_text(r.text, encoding="utf-8")
            print(f"      {r.route:6} {'ok ' if r.ok else 'X  '} {r.status} {r.length:6}자 {r.seconds:5.1f}s {r.error}", flush=True)
        login, login_rule = judge_login(routes.get("server"), routes.get("tab"))
        trunc, trunc_rule = judge_truncated(routes.get("tab"))
        best, why = pick_best(list(routes.values()))
        print(f"      → 로그인:{login} {login_rule} / 잘림:{trunc} {trunc_rule} / 고름:{best} {why}", flush=True)
        for r in routes.values():
            d = {k: v for k, v in asdict(r).items() if k not in ("text", "html")}
            d.update(login=login, login_rule=login_rule, truncated=trunc, truncated_rule=trunc_rule, best=best, best_reason=why)
            out.append(d)
        time.sleep(1.0)                              # 같은 사이트에 몰아치지 않기 (05 4절)
    write_measure(out); write_scoring_template(load_links(a.only, None))
    print(f"\nmeasure.csv {len(out)}줄, 채점.csv 준비됨")


if __name__ == "__main__":
    main()
