# mfstockupdater

マネーフォワードの外国株式資産を、前取引日の終値・為替レートで自動更新するツール。
毎月1日 AM 4:00 に LaunchAgent が `mfe.py --last-month` を実行して前月分を処理する。

## プライバシールール（最重要）

**ドキュメント・コード・コミットメッセージに以下の情報を絶対に書かない：**

- 具体的な銘柄コード（例: AAPL, ACWI など）
- 資産名・口座名（例: #ETRADE-... など）
- 保有株数・保有金額
- ログの出力内容（銘柄・金額が含まれる）

代わりに「登録済み `#` 形式の全資産」「対象銘柄」などの一般表現を使うこと。

## 実行環境

- **LaunchAgent**: `~/Library/LaunchAgents/com.user.mfstockupdater.plist`
- **スクリプト**: `/usr/local/bin/mfe.py` → `mfe.py`（シンボリックリンク）
- **Python**: `/opt/anaconda3/bin/python`
- **ログ**: `~/Library/Logs/mfstockupdater/mf_stderr.log`

環境変数（plist で管理、git管理外）:
- `MF_ID` / `MF_PASS` / `MF_TWO_STEP_VERIFICATION_TOTP_SECRET_KEY`
- `ALPHAVANTAGE_API_KEY`
- `STOCK_PRICE_SOURCE`: `yfinance`（デフォルト）または `alphavantage`

## 株価取得

`mfe.py` は処理開始前に全期間・全銘柄の株価を一括プリフェッチしてキャッシュに保存する。
個別取得は通常発生しない（キャッシュミス時のみ）。

| `STOCK_PRICE_SOURCE` | 動作 |
|---|---|
| `yfinance`（デフォルト） | yfinance で一括取得。失敗時は Alpha Vantage にフォールバック |
| `alphavantage` | Alpha Vantage `TIME_SERIES_DAILY&outputsize=compact` で一括取得 |

為替レートは三菱UFJ銀行（`getHistoricalCurrency.py`）から取得。`percache` でローカルキャッシュ済み。

## 注意事項

- **plistは直接編集しない**: 認証情報が平文で含まれているため、git にコミットしない
- **ログファイルはgit管理外**: 銘柄・金額情報が含まれるため
- **yfinance レートリミット**: 大量の日付範囲を処理する場合は Alpha Vantage にフォールバックされる
