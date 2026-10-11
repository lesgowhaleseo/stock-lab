# -*- coding: utf-8 -*-
"""
stock-lab · 과거 10년 테스트(백테스트)

무엇을 하나요?
  국내(코스피 대형주)와 미국(S&P500) 종목의 과거 주가를 받아서,
  전략별로 가상 1,000만 원을 굴렸다면 어땠을지 계산하고
  results/ 폴더에 보고서(report.md), 그래프, 거래 기록을 남깁니다.

기본 규칙
  - 신호는 그날 종가로 판단하고, 주문은 다음 날 시가에 체결 (미래 엿보기 방지)
  - 정수 주식만 매수, 수수료·매도세 반영
  - ④마법공식은 과거 재무 데이터가 무료로 없어서 실시간 모의에서만 평가
  - ⑥Claude 판단은 과거를 이미 알고 있어 불공정하므로 실시간 모의에서만 평가
"""
import io
import os
import time

import numpy as np
import pandas as pd

# ───────────────────────── 설정 ─────────────────────────
START_DATA = "2015-01-01"   # 지표 계산용 준비 기간 포함
START_SIM = "2016-01-01"    # 실제 테스트 시작
INITIAL_KRW = 10_000_000    # 전략별 가상 금액
N_HOLD = 10                 # ①②③ 보유 종목 수
N_REBOUND = 5               # ⑤ 동시 보유 종목 수
OUT = "results"
SYNTHETIC = os.environ.get("STOCKLAB_SYNTHETIC") == "1"  # 코드 점검용 가짜 데이터

MARKETS = {
    "kr": {"label": "국내", "fee": 0.00015, "sell_tax": 0.0020,
           "index": "^KS11", "etf": "069500.KS", "cur": "원"},
    "us": {"label": "미국", "fee": 0.0007, "sell_tax": 0.0,
           "index": "^GSPC", "etf": "SPY", "cur": "달러"},
}

STRATEGIES = {
    "s1": ("①역발상", "S1 Contrarian"),
    "s2": ("②소외우량주", "S2 Neglected"),
    "s3": ("③모멘텀", "S3 Momentum"),
    "s5": ("⑤단기반등", "S5 Rebound"),
    "s7": ("⑦기준선(지수)", "S7 Index (baseline)"),
}

UA = {"User-Agent": "Mozilla/5.0 (stock-lab backtest)"}

KR_FALLBACK = """005930 삼성전자,000660 SK하이닉스,373220 LG에너지솔루션,207940 삼성바이오로직스,
005380 현대차,000270 기아,068270 셀트리온,005490 POSCO홀딩스,035420 NAVER,035720 카카오,
051910 LG화학,006400 삼성SDI,105560 KB금융,055550 신한지주,012330 현대모비스,028260 삼성물산,
066570 LG전자,003550 LG,032830 삼성생명,015760 한국전력,017670 SK텔레콤,030200 KT,034730 SK,
096770 SK이노베이션,086790 하나금융지주,316140 우리금융지주,009150 삼성전기,018260 삼성에스디에스,
033780 KT&G,010130 고려아연,011200 HMM,003670 포스코퓨처엠,329180 HD현대중공업,012450 한화에어로스페이스,
042660 한화오션,009540 HD한국조선해양,024110 기업은행,000810 삼성화재,010950 S-Oil,090430 아모레퍼시픽,
051900 LG생활건강,036570 엔씨소프트,251270 넷마블,011170 롯데케미칼,097950 CJ제일제당,004020 현대제철,
000100 유한양행,128940 한미약품,047050 포스코인터내셔널,267250 HD현대,034020 두산에너빌리티,
011070 LG이노텍,161390 한국타이어앤테크놀로지,006800 미래에셋증권,323410 카카오뱅크,259960 크래프톤,
352820 하이브,302440 SK바이오사이언스,064350 현대로템"""

US_FALLBACK = ("AAPL MSFT NVDA AMZN GOOGL META BRK-B JPM V JNJ WMT PG XOM UNH HD MA CVX KO PEP "
               "ABBV MRK COST AVGO ADBE CRM TMO MCD CSCO ACN LIN ABT DHR NKE TXN NEE PM ORCL AMD "
               "INTC QCOM HON UPS IBM CAT GS MS BA GE LOW SBUX AMGN").split()


# ───────────────────────── 데이터 ─────────────────────────
def kr_universe(n=150):
    """코스피 시가총액 상위 종목 (우선주 제외). 실패하면 대표 종목 목록 사용."""
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KOSPI")
        df = df[~df["Name"].str.contains(r"우$|우B$|우\(|스팩", regex=True)]
        df = df.sort_values("Marcap", ascending=False).head(n)
        syms = [f"{c}.KS" for c in df["Code"]]
        if len(syms) >= 50:
            return syms, dict(zip(syms, df["Name"]))
    except Exception as e:
        print("국내 종목 목록 받기 실패, 대표 종목으로 대체:", e)
    pairs = [p.strip().split(" ", 1) for p in KR_FALLBACK.replace("\n", "").split(",")]
    syms = [f"{c}.KS" for c, _ in pairs]
    return syms, {f"{c}.KS": nm for c, nm in pairs}


def us_universe():
    """S&P500 구성 종목. 실패하면 대표 종목 목록 사용."""
    try:
        import requests
        html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                            headers=UA, timeout=30).text
        t = pd.read_html(io.StringIO(html))[0]
        syms = t["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
        if len(syms) > 400:
            return syms, dict(zip(syms, t["Security"]))
    except Exception as e:
        print("S&P500 목록 받기 실패, 대표 종목으로 대체:", e)
    return US_FALLBACK, {s: s for s in US_FALLBACK}


def yf_download(tickers, chunk=80):
    """야후 파이낸스에서 수정주가(배당·액면분할 반영)로 시가·종가·거래량을 받음."""
    import yfinance as yf
    parts = {"Open": [], "Close": [], "Volume": []}
    for k in range(0, len(tickers), chunk):
        batch = tickers[k:k + chunk]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(batch, start=START_DATA, auto_adjust=True,
                                 progress=False, group_by="column", threads=True)
                break
            except Exception as e:
                print(f"  다운로드 재시도({attempt + 1}):", e)
                time.sleep(15 * (attempt + 1))
        if df is None or df.empty:
            continue
        if not isinstance(df.columns, pd.MultiIndex):
            df.columns = pd.MultiIndex.from_product([df.columns, batch])
        for f in parts:
            parts[f].append(df[f])
        print(f"  {min(k + chunk, len(tickers))}/{len(tickers)} 종목 받음")
        time.sleep(2)
    return [pd.concat(parts[f], axis=1) for f in ("Open", "Close", "Volume")]


def load_market(mkey):
    m = MARKETS[mkey]
    if SYNTHETIC:
        return synthetic_market(mkey)
    syms, names = kr_universe() if mkey == "kr" else us_universe()
    print(f"[{m['label']}] 종목 {len(syms)}개 데이터 받는 중...")
    O, C, V = yf_download(syms + [m["etf"], m["index"]])
    idx = C[m["index"]]
    O, C, V = (x.drop(columns=[m["index"]], errors="ignore") for x in (O, C, V))
    fx = None
    if mkey == "us":
        _, fxc, _ = yf_download(["KRW=X"])
        fx = fxc["KRW=X"]
    return O, C, V, idx, names, fx


def synthetic_market(mkey):
    """인터넷 없이 코드만 점검할 때 쓰는 가짜 주가."""
    rng = np.random.default_rng(0 if mkey == "kr" else 1)
    dates = pd.bdate_range(START_DATA, "2025-12-31")
    n = 40
    tick = [f"T{i:02d}" for i in range(n)]
    mkt = np.cumsum(rng.normal(0.0003, 0.011, len(dates)))
    rets = rng.normal(0.0002, 0.018, (len(dates), n)) + np.diff(np.r_[0, mkt])[:, None]
    C = pd.DataFrame(100 * np.exp(np.cumsum(rets, 0)), dates, tick)
    O = C.shift(1) * (1 + rng.normal(0, 0.004, C.shape))
    V = pd.DataFrame(rng.lognormal(12, 0.5, C.shape), dates, tick)
    etf = MARKETS[mkey]["etf"]
    C[etf] = 100 * np.exp(mkt)
    O[etf] = C[etf].shift(1)
    V[etf] = 1e6
    idx = C[etf] * 30
    fx = pd.Series(1200 + np.cumsum(rng.normal(0, 3, len(dates))), dates) if mkey == "us" else None
    if mkey == "kr":
        C, O = C * 300, O * 300
    return O, C, V, idx, {t: t for t in tick}, fx


# ───────────────────────── 지표 ─────────────────────────
def regimes(idx):
    """시장 상황 4칸: 추세(지수 200일선 위/아래) × 흔들림(20일 변동성이 1년 중앙값보다 큰가)."""
    trend = np.where(idx > idx.rolling(200).mean(), "상승장", "하락장")
    vol = idx.pct_change().rolling(20).std()
    shaky = np.where(vol > vol.rolling(250, min_periods=120).median(), "요동", "잔잔")
    return pd.Series([f"{a}·{b}" for a, b in zip(trend, shaky)], idx.index)


def features(C, V):
    sma200 = C.rolling(200).mean()
    return {
        "close": C,
        "ret3": C / C.shift(3) - 1,
        "ret20": C / C.shift(20) - 1,
        "ret250": C / C.shift(250) - 1,
        "mom": C.shift(21) / C.shift(252) - 1,          # 최근 1달 제외 1년 수익률
        "c_over_sma": C / sma200,
        "vol_ratio": V.rolling(60).mean() / V.rolling(250).mean(),  # 최근 거래량 / 1년 거래량
        "volat60": C.pct_change().rolling(60).std(),
        "valid": C.notna() & C.shift(250).notna(),      # 상장 1년 이상 + 오늘 거래됨
    }


# ───────────────────────── 포트폴리오 ─────────────────────────
class Portfolio:
    def __init__(self, key, cash, fee, tax, names):
        self.key, self.cash, self.fee, self.tax, self.names = key, cash, fee, tax, names
        self.pos = {}      # 종목 -> 보유 정보
        self.trades = []   # 사고팔고 끝난 거래
        self.equity = []

    def value(self, closes):
        v = self.cash
        for t, p in self.pos.items():
            px = closes.get(t)
            v += p["shares"] * (px if pd.notna(px) else p["avg"])
        return v

    def buy(self, t, px, budget, i, d, regime, why):
        if t in self.pos or not (pd.notna(px) and px > 0):
            return
        shares = int(min(budget, self.cash) // (px * (1 + self.fee)))
        if shares <= 0:
            return
        cost = shares * px * (1 + self.fee)
        self.cash -= cost
        self.pos[t] = dict(shares=shares, avg=cost / shares, entry_i=i, entry_d=d,
                           regime=regime, why=why)

    def sell(self, t, px, i, d, why):
        p = self.pos.get(t)
        if p is None or not (pd.notna(px) and px > 0):
            return
        proceeds = p["shares"] * px * (1 - self.fee - self.tax)
        self.cash += proceeds
        self._record(t, p, d, i, proceeds / (p["shares"] * p["avg"]) - 1, why)
        del self.pos[t]

    def _record(self, t, p, d, i, ret, why):
        self.trades.append({
            "전략": STRATEGIES[self.key][0], "종목": t, "이름": self.names.get(t, t),
            "매수일": p["entry_d"].date(), "매도일": d if isinstance(d, str) else d.date(),
            "보유일": i - p["entry_i"], "수익률": round(ret, 4),
            "매수 시 상황": p["regime"], "매수 이유": p["why"], "매도 이유": why,
        })

    def close_out(self, closes, i):
        for t, p in list(self.pos.items()):
            px = closes.get(t)
            ret = (px * (1 - self.fee - self.tax)) / p["avg"] - 1 if pd.notna(px) else 0
            self._record(t, p, "보유중", i, ret, "테스트 종료 시점 평가")


def rebalance(pf, top, keep, n, eq, why_buy, why_sell):
    """keep 밖으로 밀려난 종목은 팔고, 빈자리는 top 순서대로 채움 (잦은 교체 방지)."""
    orders = [("sell", t, why_sell) for t in pf.pos if t not in keep]
    slots = n - sum(1 for t in pf.pos if t in keep)
    for t in top:
        if slots <= 0:
            break
        if t not in pf.pos:
            orders.append(("buy", t, eq / n, why_buy))
            slots -= 1
    return orders


# ───────────────────────── 전략 ─────────────────────────
def s1_contrarian(pf, i, r, eq, st):
    """시장이 60일 고점 대비 8% 이상 빠진 공포 구간에, 원래 멀쩡했는데 최근 많이 빠진 종목을 삼."""
    dd = r["idx_dd60"]
    if pf.pos:
        if dd > -0.02:
            return [("sell", t, "시장 회복 → 정리") for t in pf.pos]
        if i - min(p["entry_i"] for p in pf.pos.values()) >= 60:
            return [("sell", t, "60거래일 경과 → 정리") for t in pf.pos]
        return []
    if pd.notna(dd) and dd <= -0.08:
        cand = r["valid"] & (r["mom"] > -0.10) & (r["ret20"] < 0)
        picks = r["ret20"][cand].nsmallest(N_HOLD).index
        why = f"시장 공포(60일 고점 대비 {dd:.0%}) 속 급락한 종목"
        return [("buy", t, eq / N_HOLD, why) for t in picks]
    return []


def s2_neglected(pf, i, r, eq, st):
    """매주: 거래량(관심)은 줄었지만 1년 성적·장기 추세는 무너지지 않은 조용한 종목 (가격·거래량 간이판)."""
    if (i - st["start"]) % 5:
        return []
    cand = r["valid"] & (r["vol_ratio"] < 0.7) & (r["ret250"] > -0.2) & (r["c_over_sma"] > 0.9)
    score = (r["vol_ratio"][cand].rank() + r["volat60"][cand].rank()).sort_values()
    ranked = list(score.index)
    return rebalance(pf, ranked[:N_HOLD], set(ranked[:2 * N_HOLD]), N_HOLD, eq,
                     "관심(거래량) 감소 + 장기 추세 유지", "조건에서 벗어나 교체")


def s3_momentum(pf, i, r, eq, st):
    """매달: 최근 1년(직전 1달 제외) 가장 많이 오른 종목."""
    if (i - st["start"]) % 21:
        return []
    m = r["mom"][r["valid"]].dropna().sort_values(ascending=False)
    ranked = list(m.index)
    return rebalance(pf, ranked[:N_HOLD], set(ranked[:2 * N_HOLD]), N_HOLD, eq,
                     "1년 상승률 상위", "상승률 순위 밖으로 밀려 교체")


def s5_rebound(pf, i, r, eq, st):
    """매일: 상승 추세인 종목이 3일간 7% 이상 급락하면 사서, +3% 또는 5거래일 뒤 팜."""
    orders = []
    for t, p in pf.pos.items():
        px = r["close"].get(t)
        if pd.notna(px) and px / p["avg"] - 1 >= 0.03:
            orders.append(("sell", t, "목표 수익 +3% 도달"))
        elif i - p["entry_i"] >= 5:
            orders.append(("sell", t, "5거래일 경과"))
    slots = N_REBOUND - (len(pf.pos) - len(orders))
    if slots > 0:
        cand = r["valid"] & (r["ret3"] <= -0.07) & (r["c_over_sma"] > 1.0)
        picks = [t for t in r["ret3"][cand].sort_values().index if t not in pf.pos][:slots]
        orders += [("buy", t, eq / N_REBOUND, "상승 추세 종목의 3일 급락 → 반등 기대") for t in picks]
    return orders


def s7_baseline(pf, i, r, eq, st):
    """지수 ETF를 처음에 사서 계속 보유."""
    if not pf.pos and not st.get("bought"):
        st["bought"] = True
        return [("buy", st["etf"], pf.cash, "지수 ETF 매수 후 보유")]
    return []


FUNCS = {"s1": s1_contrarian, "s2": s2_neglected, "s3": s3_momentum,
         "s5": s5_rebound, "s7": s7_baseline}


# ───────────────────────── 시뮬레이션 ─────────────────────────
def simulate(mkey):
    m = MARKETS[mkey]
    O, C, V, idx, names, fx = load_market(mkey)
    # 대부분 종목이 비어 있는 날(휴장일 잡음)은 제거
    good = C.notna().mean(axis=1) > 0.5
    O, C, V = O[good], C[good], V[good]
    dates = C.index
    idx = idx.reindex(dates).ffill()
    stocks = [c for c in C.columns if c != m["etf"]]
    F = features(C[stocks], V[stocks])
    reg = regimes(idx)
    idx_dd60 = idx / idx.rolling(60).max() - 1
    Cf = C.ffill()
    s0 = int(dates.searchsorted(pd.Timestamp(START_SIM)))
    if fx is not None:
        fx = fx.reindex(dates).ffill().bfill()
    start_cash = INITIAL_KRW if fx is None else INITIAL_KRW / fx.iloc[s0]

    pfs = {k: Portfolio(k, start_cash, m["fee"], m["sell_tax"], names) for k in FUNCS}
    states = {k: {"start": s0, "etf": m["etf"]} for k in FUNCS}
    pending = {k: ([], None) for k in FUNCS}

    print(f"[{m['label']}] 시뮬레이션 {dates[s0].date()} ~ {dates[-1].date()}")
    for i in range(s0, len(dates)):
        d = dates[i]
        opens, closes = O.iloc[i], Cf.iloc[i]
        r = {k: v.iloc[i] for k, v in F.items()}
        r["idx_dd60"] = idx_dd60.iloc[i]
        for k, pf in pfs.items():
            orders, regime = pending[k]
            for o in orders:                       # 어제 결정한 주문을 오늘 시가에 체결
                if o[0] == "sell":
                    pf.sell(o[1], opens.get(o[1]), i, d, o[2])
            for o in orders:
                if o[0] == "buy":
                    pf.buy(o[1], opens.get(o[1]), o[2], i, d, regime, o[3])
            eq = pf.value(closes)
            pf.equity.append(eq)
            pending[k] = (FUNCS[k](pf, i, r, eq, states[k]), reg.iloc[i])

    sim_dates = dates[s0:]
    equity = pd.DataFrame({k: pf.equity for k, pf in pfs.items()}, index=sim_dates)
    if fx is not None:
        equity = equity.mul(fx.iloc[s0:], axis=0)   # 원화 환산 (환율 효과 포함)
    for pf in pfs.values():
        pf.close_out(Cf.iloc[-1], len(dates) - 1)
    trades = pd.DataFrame([t for pf in pfs.values() for t in pf.trades])
    if not trades.empty:
        trades.insert(0, "시장", m["label"])
    return equity, trades, reg.reindex(sim_dates)


# ───────────────────────── 보고서 ─────────────────────────
def pct(x):
    return "-" if pd.isna(x) else f"{x * 100:+.1f}%"


def md_table(header, rows):
    s = "| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n"
    return s + "".join("| " + " | ".join(str(c) for c in row) + " |\n" for row in rows)


def summarize(mkey, equity, trades, reg):
    m = MARKETS[mkey]
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    rows = []
    for k in equity.columns:
        e = equity[k]
        tr = trades[trades["전략"] == STRATEGIES[k][0]] if not trades.empty else trades
        done = tr[tr["매도일"] != "보유중"] if not tr.empty else tr
        win = (done["수익률"] > 0).mean() if len(done) else np.nan
        rows.append([STRATEGIES[k][0], f"{e.iloc[-1]:,.0f}원", pct(e.iloc[-1] / e.iloc[0] - 1),
                     pct((e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1),
                     pct((e / e.cummax() - 1).min()), len(done),
                     "-" if pd.isna(win) else f"{win * 100:.0f}%"])
    out = f"### {m['label']} 시장 성적표\n\n"
    out += md_table(["전략", "최종 금액", "총수익률", "연평균", "최대 낙폭", "완료 거래", "승률"], rows)

    # 상황별 성적표: 그날 아침에 알고 있던 상황(전날 종가 기준) 별로 하루 수익률 평균 → 연환산
    daily = equity.pct_change()
    lab = reg.shift(1)
    cats = ["상승장·잔잔", "상승장·요동", "하락장·잔잔", "하락장·요동"]
    share = lab.value_counts(normalize=True)
    hdr = ["전략"] + [f"{c} ({share.get(c, 0) * 100:.0f}%)" for c in cats]
    rows = []
    for k in equity.columns:
        g = daily[k].groupby(lab).mean() * 252
        rows.append([STRATEGIES[k][0]] + [pct(g.get(c)) for c in cats])
    out += f"\n**상황별 성적 (연환산 수익률, 괄호는 그 상황이었던 기간 비중)**\n\n"
    out += md_table(hdr, rows)

    if not trades.empty:
        done = trades[trades["매도일"] != "보유중"]
        g = done.groupby(["전략", "매수 시 상황"])["수익률"]
        tab = pd.DataFrame({"n": g.size(), "win": g.apply(lambda x: (x > 0).mean()), "avg": g.mean()})
        rows = [[s, c, int(v.n), f"{v.win * 100:.0f}%", pct(v.avg)] for (s, c), v in tab.iterrows()]
        out += f"\n**매수 시점 상황별 거래 결과**\n\n"
        out += md_table(["전략", "매수 시 상황", "거래 수", "승률", "평균 수익률"], rows)
    out += f"\n![{mkey} equity]({mkey}_equity.png)\n\n"
    return out


def plot(mkey, equity):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    for k in equity.columns:
        ax.plot(equity.index, equity[k] / 1e6, label=STRATEGIES[k][1],
                lw=2.2 if k == "s7" else 1.3, ls="--" if k == "s7" else "-")
    ax.set_title(f"{mkey.upper()} market: value of 10M KRW per strategy")
    ax.set_ylabel("KRW (millions)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{OUT}/{mkey}_equity.png", dpi=120)
    plt.close(fig)


def main():
    os.makedirs(OUT, exist_ok=True)
    report = "# stock-lab 과거 테스트 결과\n\n"
    report += (f"- 기간: {START_SIM} ~ 최근, 전략별 가상 {INITIAL_KRW:,}원 (미국은 시작 시점 환율로 달러 환전)\n"
               "- 신호는 종가로 판단 → 다음 날 시가에 체결, 정수 주식만, 수수료·매도세 반영\n"
               "- 미국 결과는 원화 환산이라 환율 변동이 포함돼 있어요\n\n"
               "> ⚠️ 꼭 감안할 점: 종목 명단을 '지금' 기준으로 썼기 때문에, 지난 10년간 망하거나 "
               "지수에서 빠진 회사가 빠져 있어요. 그래서 결과가 실제보다 좋아 보이는 경향이 있습니다. "
               "전략 간 '성격 비교'용으로 보세요. 미국 주식 양도소득세는 반영하지 않았고, "
               "②는 재무 데이터 없이 가격·거래량만 쓴 간이판입니다. ④마법공식과 ⑥Claude 판단은 실시간 모의에서 평가해요.\n\n")
    all_trades = []
    for mkey in ("kr", "us"):
        equity, trades, reg = simulate(mkey)
        equity.round(0).to_csv(f"{OUT}/{mkey}_equity.csv", encoding="utf-8-sig")
        plot(mkey, equity)
        report += summarize(mkey, equity, trades, reg)
        all_trades.append(trades)
    pd.concat(all_trades).to_csv(f"{OUT}/trades.csv", index=False, encoding="utf-8-sig")
    report += ("## 파일 안내\n\n- `trades.csv`: 모든 거래 기록 (엑셀로 열면 돼요)\n"
               "- `kr_equity.csv`, `us_equity.csv`: 전략별 매일 평가금액\n")
    with open(f"{OUT}/report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("완료! results/report.md 를 확인하세요.")


if __name__ == "__main__":
    main()
