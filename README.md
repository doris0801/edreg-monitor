# E-dReg 每日追蹤器

資料來源：台電電力交易平台「合格交易者資訊 / 民間合格交易者」。
目標欄位：電能移轉複合動態調節備轉容量（E-dReg）。

## 本機執行

```bash
pip install -r requirements.txt
playwright install chromium
python edreg_tracker.py
```

## 輸出

- `data/snapshots/qse_edreg_YYYY-MM-DD.csv`：每日完整 E-dReg 供給者清單
- `data/latest_edreg.csv`：最新完整清單
- `data/reports/changes_YYYY-MM-DD.csv`：只顯示：
  - `new_provider`：新加入 E-dReg 的業者
  - `capacity_increase`：舊業者 E-dReg 容量增加

## GitHub Actions

把 `.github/workflows/edreg_daily.yml` 放入 repo 後，GitHub 會每天台灣時間 09:10 自動跑一次。也可以在 Actions 手動 Run workflow。
