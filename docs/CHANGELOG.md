# Changelog

## 2026-06-28〜06-29

### 修正1: シンボリックリンクの張り直し

**問題:** リポジトリを `~/Documents/GitHub/mfstockupdater/` から
`~/Documents/development/myAgentTeams/projects/mfstockupdater/` に移動したため、
LaunchAgent が参照するシンボリックリンクが壊れていた。

```
/usr/local/bin/mfe.py → /Users/shiba/Documents/GitHub/mfstockupdater/mfe.py  # 存在しない
```

**修正:**
```bash
ln -sf /Users/shiba/Documents/development/myAgentTeams/projects/mfstockupdater/mfe.py /usr/local/bin/mfe.py
```

---

### 修正2: 株価の一括プリフェッチ実装（`mfe.py`）

**問題:** `get_historical_stock_price_yf()` が日付ごとに yfinance へ個別リクエストを送っていた。
バックフィル（5ヶ月分 ≈ 150日 × 2銘柄）を実行したところ `YFRateLimitError` が多発し、ほぼ全件スキップされた。

**修正:** 以下の2メソッドを `MoneyForwardEditor` クラスに追加。

- `get_tickers_from_page(date)` — 処理開始日の履歴ページから対象銘柄のティッカーを収集
- `prefetch_stock_prices(tickers, start_date, end_date)` — 全銘柄・全期間の株価を処理前に一括取得してキャッシュに保存。yfinance が失敗した場合は `_prefetch_stock_prices_alphavantage()` にフォールバック
- `_prefetch_stock_prices_alphavantage(tickers, start_date, end_date)` — Alpha Vantage の `TIME_SERIES_DAILY&outputsize=compact`（無料プラン対応）で一括取得

`__main__` で `choseGroup()` の直後にプリフェッチを実行するよう変更。

**効果:** yfinance の API コール数を「日数 × 銘柄数」回 → 「銘柄数」回（= 2回）に削減。

---

### バックフィル実行

LaunchAgent が実行されなかった 2026年2月〜6月27日分を手動実行。

```bash
/opt/anaconda3/bin/python /usr/local/bin/mfe.py --start-date 2026-02-01 --end-date 2026-06-27
```

- 対象資産: 登録済み `#` 形式の全資産
- 結果: スキップ・エラーなし、全件正常処理

---

### 修正3: 株価取得ソースの切り替え機能追加（`mfe.py`）

環境変数 `STOCK_PRICE_SOURCE` で株価取得APIを切り替えられるようにした。

| 値 | 動作 |
|----|------|
| `yfinance`（デフォルト） | yfinance で一括取得。失敗時は Alpha Vantage にフォールバック |
| `alphavantage` | Alpha Vantage `TIME_SERIES_DAILY&outputsize=compact` で一括取得 |

切り替え方法（plist の `EnvironmentVariables` に追加）:
```xml
<key>STOCK_PRICE_SOURCE</key>
<string>yfinance</string>
```

変更箇所:
- `__init__`: `self.stock_price_source` を追加
- `prefetch_stock_prices()`: ソースに応じて `_prefetch_stock_prices_yfinance()` または `_prefetch_stock_prices_alphavantage()` を呼ぶように変更
- `_prefetch_stock_prices_yfinance()`: yfinanceの一括取得ロジックを独立メソッドに分離
- `edit_history()`: キャッシュミス時の個別取得もソースに応じて切り替え

---

### 調査で判明した未修正の問題

| 項目 | 内容 |
|------|------|
| `mf.py` の構文エラー | `portfolio()` 関数内のトリプルクォート構造が破損、`IndentationError` が発生。現在は LaunchAgent から呼ばれていないため実害なし |
| `Dockerfile` のビルド不可 | ベースイメージを Alpine → Debian (`python:3.11-slim`) に変更したが `apk add` が残存。AWS ECS Fargate 経由での実行が必要な場合は要修正 |
| 3〜6月の自動実行スキップ | 2026年3月〜6月の LaunchAgent 実行ログが存在しない。Mac のスリープが原因と推定（`StartCalendarInterval` はスリープ中はスキップされる） |
