"""
台電 ETP 合格交易者 E-dReg 每日追蹤器
抓取： https://etp.taipower.com.tw/web/qse_info/qse_list
輸出： data/snapshots/qse_edreg_YYYY-MM-DD.csv
      data/reports/changes_YYYY-MM-DD.csv
      data/latest_edreg.csv

使用：
  pip install -r requirements.txt
  playwright install chromium
  python edreg_tracker.py
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://etp.taipower.com.tw/web/qse_info/qse_list"
SERVICE_KEYWORDS = ["E-dReg", "電能移轉複合動態調節備轉容量", "電能移轉複合動\n態調節備轉容量"]

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
REPORT_DIR = DATA_DIR / "reports"
LATEST_FILE = DATA_DIR / "latest_edreg.csv"


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def clean_text(x) -> str:
    if pd.isna(x):
        return ""
    return re.sub(r"\s+", " ", str(x)).strip()


def parse_mw(x) -> float:
    """把 '1,133.9 MW'、'100'、'—' 轉成 float；空值回傳 0。"""
    if pd.isna(x):
        return 0.0
    s = str(x).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else 0.0


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [clean_text(c) for c in df.columns]
    return df


def find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = list(df.columns)
    for cand in candidates:
        for col in cols:
            if cand in col:
                return col
    return None


def standardize_edreg_table(df: pd.DataFrame) -> pd.DataFrame:
    """從網站表格中抽出公司/簡稱/母集團/E-dReg容量。"""
    df = normalize_columns(df)

    company_col = find_col(df, ["公司名稱", "交易者名稱", "合格交易者", "業者名稱"])
    short_col = find_col(df, ["公司簡稱", "簡稱"])
    group_col = find_col(df, ["母集團", "集團"])
    edreg_col = None
    for col in df.columns:
        compact = col.replace(" ", "").replace("\n", "")
        if "電能移轉複合動態調節備轉容量" in compact or "E-dReg" in col:
            edreg_col = col
            break

    if not company_col or not edreg_col:
        raise ValueError(f"搵唔到公司欄或 E-dReg 欄。網站欄位：{list(df.columns)}")

    out = pd.DataFrame({
        "snapshot_date": today_str(),
        "mother_group": df[group_col].map(clean_text) if group_col else "",
        "company_name": df[company_col].map(clean_text),
        "company_short_name": df[short_col].map(clean_text) if short_col else "",
        "edreg_mw": df[edreg_col].map(parse_mw),
        "source_url": URL,
    })

    out = out[out["company_name"].ne("")].copy()
    out = out[out["edreg_mw"] > 0].copy()  # 只保留有 E-dReg 容量的業者
    out = out.drop_duplicates(subset=["company_name"], keep="last")
    out = out.sort_values(["edreg_mw", "company_name"], ascending=[False, True]).reset_index(drop=True)
    return out


def scrape_with_playwright(headless: bool = True) -> pd.DataFrame:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(locale="zh-TW")
        page.goto(URL, wait_until="commit", timeout=180_000)
        page.wait_for_timeout(5000)

        # 有些 React 表格會慢少少；等到關鍵字出現。
        try:
            page.wait_for_selector(f"text={SERVICE_KEYWORDS[0]}", timeout=20_000)
        except PlaywrightTimeoutError:
            page.wait_for_selector(f"text=電能移轉", timeout=20_000)

        html = page.content()
        browser.close()

    tables = pd.read_html(html)
    if not tables:
        raise RuntimeError("頁面載入成功，但搵唔到 HTML table。可能網站改成虛擬表格，需要改 DOM 抽取。")

    errors = []
    for df in tables:
        text = " ".join([str(c) for c in df.columns]) + " " + " ".join(df.astype(str).head(3).to_numpy().ravel())
        if any(k.replace("\n", "") in text.replace("\n", "") for k in SERVICE_KEYWORDS):
            try:
                return standardize_edreg_table(df)
            except Exception as e:
                errors.append(str(e))

    # fallback：逐張表嘗試，避免網站欄名有細微改動。
    for df in tables:
        try:
            return standardize_edreg_table(df)
        except Exception as e:
            errors.append(str(e))

    raise RuntimeError("搵到表格，但未能辨認 E-dReg 欄位。\n" + "\n".join(errors[-3:]))


def latest_previous_snapshot(today_file: Path) -> Optional[Path]:
    files = sorted(SNAPSHOT_DIR.glob("qse_edreg_*.csv"))
    files = [f for f in files if f.resolve() != today_file.resolve()]
    return files[-1] if files else None


def compare_snapshots(current: pd.DataFrame, previous: Optional[pd.DataFrame]) -> pd.DataFrame:
    if previous is None or previous.empty:
        result = current.copy()
        result["change_type"] = "first_snapshot"
        result["previous_edreg_mw"] = 0.0
        result["current_edreg_mw"] = result["edreg_mw"]
        result["delta_mw"] = result["current_edreg_mw"]
        return result[["snapshot_date", "change_type", "mother_group", "company_name", "company_short_name", "previous_edreg_mw", "current_edreg_mw", "delta_mw", "source_url"]]

    prev = previous[["company_name", "edreg_mw"]].rename(columns={"edreg_mw": "previous_edreg_mw"})
    cur = current.rename(columns={"edreg_mw": "current_edreg_mw"})
    merged = cur.merge(prev, on="company_name", how="left")
    merged["previous_edreg_mw"] = merged["previous_edreg_mw"].fillna(0.0)
    merged["delta_mw"] = merged["current_edreg_mw"] - merged["previous_edreg_mw"]

    def label(row):
        if row["previous_edreg_mw"] == 0 and row["current_edreg_mw"] > 0:
            return "new_provider"
        if row["delta_mw"] > 0:
            return "capacity_increase"
        return "no_change"

    merged["change_type"] = merged.apply(label, axis=1)
    changed = merged[merged["change_type"].isin(["new_provider", "capacity_increase"])].copy()
    return changed[["snapshot_date", "change_type", "mother_group", "company_name", "company_short_name", "previous_edreg_mw", "current_edreg_mw", "delta_mw", "source_url"]].sort_values(["change_type", "delta_mw"], ascending=[True, False])


def main() -> int:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    current = scrape_with_playwright(headless=True)
    today_file = SNAPSHOT_DIR / f"qse_edreg_{today_str()}.csv"
    current.to_csv(today_file, index=False, encoding="utf-8-sig")
    current.to_csv(LATEST_FILE, index=False, encoding="utf-8-sig")

    prev_file = latest_previous_snapshot(today_file)
    previous = pd.read_csv(prev_file) if prev_file else None
    report = compare_snapshots(current, previous)
    report_file = REPORT_DIR / f"changes_{today_str()}.csv"
    report.to_csv(report_file, index=False, encoding="utf-8-sig")

    total = current["edreg_mw"].sum()
    print(f"Done. E-dReg providers: {len(current)}, total MW: {total:,.1f}")
    print(f"Snapshot: {today_file}")
    print(f"Change report: {report_file}")
    if report.empty:
        print("No new provider or capacity increase today.")
    else:
        print(report.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
