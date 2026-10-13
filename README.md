# PWCA大会ページ更新監視

PWCA Panchgani大会ページを GitHub Actions で定期取得し、内容が変わったら Telegram に通知します。宿泊・予約・料金などのキーワードを含む変更は「宿泊関連の可能性」として優先通知します。

> 通知は参考情報です。予約開始や空室を保証しません。公式メール・主催者からの連絡も引き続き確認してください。

## 構成

```
.github/workflows/monitor.yml   GitHub Actions（schedule + 手動実行）
config/targets.yaml             監視URL・抽出ルール・キーワード・通知設定
src/pwca_monitor/
  cli.py        コマンド（self-test / test / check / notify-test / monitor）
  config.py     設定読み込み、URL組み立て（base_url + events_path + slug）
  fetcher.py    HTTP取得（タイムアウト・制限付きリトライ）
  parser.py     本文・リンク・見出しの抽出と正規化
  diff.py       差分検出（本文行・リンク・PDF・タイトル・見出し）
  classifier.py 宿泊関連キーワード判定（追加・変更部分のみ）
  notifier.py   通知文生成、Console/Telegram通知（Notifierインターフェース）
  state.py      状態JSONの読み書き
  monitor.py    1回分の監視処理
  subpages.py   大会ページからリンクされた配下ページの検出
  selftest.py   ネットワーク不要の自己テストシナリオ
tests/          pytest と HTML fixture（実ページのスナップショットを含む）
```

監視対象は2大会で、それぞれ「大会ページ」と「その大会の配下ページ」を監視します。状態保存と通知は大会ごと・ページごとに分かれています。

処理の流れ: 取得 → 抽出（`[data-page-events-show]` 内のみ。カウントダウン・ニュース・SNS・スポンサー・選手リストは除外）→ 前回状態と比較 → 分類 → 通知 → 状態保存。

## ローカルのセットアップ

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

以下のコマンドは `PYTHONPATH=src .venv/bin/python -m pwca_monitor <mode>` の形式で実行します。

| コマンド | 内容 | ネットワーク | 状態変更 |
|---|---|---|---|
| `.venv/bin/python -m pytest -q` | 単体テスト | なし | なし |
| `self-test` | 追加/変更/削除・キーワード・重複抑止・取得失敗・通知失敗などのシナリオ | なし | なし |
| `test` | fixture（before→after）で初回・無変更・更新・再処理を通し、通知文をコンソール表示 | なし | なし |
| `check --dry-run [--show-text] [--state PATH]` | 実サイトを1回ずつ取得して、HTTP・最終URL・タイトル・文字数・リンクを表示 | あり | なし（checkは常に読み取りのみ） |
| `notify-test` | Telegramにテストメッセージを1件だけ送信 | Telegram | なし |
| `test --send-telegram` / `self-test --send-telegram` | 上記テストに加え、Telegramに1件送信 | Telegram | なし |
| `monitor --state PATH [--dry-run] [--console]` | 本番監視を1回実行。`--dry-run` は状態を保存せずコンソール表示、`--console` は状態を保存しコンソール表示 | あり | あり |

## Telegram の設定

1. Telegramで `@BotFather` に `/newbot` を送ってBotを作成し、表示されたトークンを控えます（**会話・コード・Issueに貼らない**）。
2. 作成したBotとのチャットを開いて、何か1通送ります。
3. ブラウザで `https://api.telegram.org/bot<トークン>/getUpdates` を開き、`"chat":{"id": ...}` の数値を確認します。これが Chat ID です（このURLにはトークンが含まれるので、共有・スクショしないでください）。
4. ローカルで試す場合は `.env.example` を `.env` にコピーして値を入れ、次を実行します（`.env` は `.gitignore` 済み）。
   ```bash
   set -a; source .env; set +a
   PYTHONPATH=src .venv/bin/python -m pwca_monitor notify-test
   ```
   トークンが誤っていると `HTTP 401: Unauthorized`、Chat IDが誤っていると `HTTP 400: Bad Request: chat not found` と表示されます。トークンはログに出力しません。

## GitHub リポジトリの作成と Secrets

1. GitHubで新しいリポジトリを作成し、このディレクトリをpushします。
   ```bash
   git init -b main && git add . && git commit -m "PWCA monitor"
   git remote add origin git@github.com:<you>/<repo>.git && git push -u origin main
   ```
   `git status` を見て、`.env` や `state/*.json` が含まれていないことを確認してください。
2. **公開/非公開の選択**
   - **Public（推奨）**: Actionsの実行時間が無料・無制限です。`monitor-state` ブランチの状態ファイル（公開大会ページの本文とリンク）も公開されますが、個人情報は含みません。
   - **Private**: 無料枠は月2,000分です。5分間隔だと1回1分換算で約8,600分/月になり、**無料枠を超えます**。Privateにする場合は cron を `*/30 * * * *` 程度に変更してください。
3. Settings → Secrets and variables → Actions → **Secrets** に `TELEGRAM_BOT_TOKEN` と `TELEGRAM_CHAT_ID` を登録します。値はGitHubの画面に直接入力し、ファイルやコードには書きません。
4. Settings → Actions → General → Workflow permissions は既定の "Read repository contents" のままで構いません。ワークフローの `monitor` ジョブだけが `contents: write` を宣言して、`GITHUB_TOKEN` で状態ブランチにpushします（Bot用のPATは不要です）。

## GitHub Actions の段階的な有効化

Actions タブ → "PWCA monitor" → "Run workflow" で、mode を選んで順に実行します。

1. `self-test`: pytest、self-test、fixtureテスト。
2. `check`: 実サイト取得のみ。2ページとも `HTTP: 200` で、本文が抽出されていることを確認します。
3. `notify-test`: Telegramに1件届くことを確認します。
4. `monitor`（1回目）: `monitor-state` ブランチが作られ、ベースラインが保存されます。通知は来ません。
5. `monitor`（2回目）: `summary: ... unchanged=[...]` になり、通知が来ないことを確認します（状態が実行をまたいで復元されていることの確認）。
6. **定期実行の有効化**: Settings → Secrets and variables → Actions → **Variables** に `MONITOR_ENABLED` = `true` を追加します。これを設定するまで、schedule のジョブは skip されます。停止するときは値を `false` にするか、Actions画面で "Disable workflow" を選びます。

`self-test` / `check` / `notify-test` は `contents: read` 権限のジョブで実行され、状態ブランチに書き込みません。

## スケジュール実行の注意と確認方法

- cron は5分間隔（`*/5`）を指定していますが、GitHubのscheduleは混雑時に数分〜数十分遅れたり、実行が抜けたりします。即時通知は保証されません。
- **Publicリポジトリで60日間活動（コミット等）がないと、scheduleは自動で無効化されます。** GitHubから事前にメールが届くので、Actions画面で "Enable workflow" を押して再開してください。状態ブランチへの1日1回のheartbeatコミットがこの活動に数えられるかは保証されていません。
- 確認方法: Actions タブ → "PWCA monitor" で、`schedule` イベントの実行が数分おきに並んでいることを確認します。`monitor-state` ブランチの `state.json` にある `last_heartbeat_at` は1日1回更新されます。
- 監視が止まっているように見えるときは、手動で `monitor` を実行するか、元ページを直接確認してください。

## 状態ファイル

- 保存先: ブランチ `monitor-state` の `state.json`。コードのブランチとは分離しており、このファイル以外はコミットしません。
- 内容: ページごとの正規化本文・リンク・見出し・ハッシュ（`pages`）、通知済み差分ハッシュ（`notified`、重複通知の抑止用）、継続中のエラー（`errors`）、`last_heartbeat_at`。
- 書き込むのは内容が変わったときだけです（更新、エラーの発生/復旧、1日1回のheartbeat）。5分ごとにはコミットしません。
- 復元・リセット: `git checkout monitor-state` で過去のコミットから `state.json` を取り出せます。ベースラインを作り直すには、ブランチを削除して `monitor` を実行します（初回扱いになり、通知は出ません）。
- 状態ファイルが壊れている場合、`monitor` はそのファイルを上書きせずに失敗し、可能ならTelegramにエラーを通知します。
- `concurrency` で同時実行を防いでいます。push に3回失敗した場合はジョブが失敗し、次回に同じ差分を再通知する可能性があります。

## 配下ページの監視

`pwca.org/events/<大会>` は1ページ完結の構成で、サイトマップもありません。そのため、配下のページは**大会ページ内のリンク**から見つけます。

- **対象**: 大会ページからリンクされていて、URLにその大会の slug を含むページ（もう一方の大会のページは対象外）。リンク先1段のみで、大会ごとに最大 `max_per_target` 件（初期値10）。
- **除外**: `subpages.exclude_url_patterns` に一致するURL。今回は開催前の宿泊・予約情報が目的なので、成績サイト `scoring.pwca.org` とリザルトページを除外しています。リザルトも監視したいときは該当行を削除してください。PDF等の添付と同ページ内アンカー（`#pilot-information`）も対象外です（PDFは大会ページの「新規リンク」として通知します）。
- **新しいページ**: 後からリンクされたページは「📄 新規ページ」として本文の抜粋付きで通知します（キーワードを含めば🏨優先）。この機能を初めて動かしたときに既にあるページは、ベースラインとして保存するだけです。
- **未公開（404）**: リンクはあるがまだ404のページはエラー扱いせず、公開されたときに「新規ページ」として通知します。一度取得できたページが404になった場合は監視エラーとして通知します。
- **リンクが消えたページ**: 監視対象から外し、そのページの状態も削除します（リンクの削除は大会ページ側の差分として通知されます）。
- **抽出**: `subpages.parser.content_selector`（`[data-flux-main]` → `main` → `body` の順に試す）。ニュース・SNS等の除外は大会ページと共通です。
- **負荷**: リクエストの間に `request.delay_between_requests` 秒（初期値1秒）の間隔を空けます。
- 2026-10-13時点では、両大会とも配下ページは0件です（大会ページ内の大会名入りリンクは成績サイトのみで、除外対象）。`check` で、検出された配下ページとその取得状況を確認できます。

## 通知の種類

| 優先度 | 条件 |
|---|---|
| 🏨 宿泊関連の可能性 | 追加・変更された本文や新規リンクのテキストにキーワードを検出。「関連キーワードを検出。内容を確認してください」と表示し、予約開始とは断定しません |
| 🔔 通常更新 | 上記以外の本文・リンク・PDF・タイトル・見出しの変更 |
| 📄 新規ページ | 大会ページから新しくリンクされた配下ページの公開（本文の抜粋付き。キーワードがあれば🏨） |
| ⚠️ 監視エラー | 403/429/5xx、タイムアウト/DNS/TLS、Content-Type不正、本文が短すぎる、抽出対象が見つからない（構造変更やログイン画面の疑い）、状態ファイル異常 |
| ✅ 復旧 | 監視エラーが解消したとき |

- 同じエラーが続く場合は、初回と、その後6時間ごと（`notify.error_renotify_hours`）にだけ通知します。
- 取得失敗時は前回の正常状態を保持します。
- 長い差分は約3,800文字で省略します。全文差分は、Actionsログの "full diff" グループと成果物 `diff-<run_id>`（14日保持）で確認できます。
- Telegram送信の失敗は最大3回リトライします（401/400 はリトライしません）。それでも失敗した場合はジョブを失敗させ、状態を更新しないので、次回の実行で再送されます。

## トラブル対応

- **403/429**: アクセス制限やレート制限の可能性があります。認証回避やプロキシ等は実装しておらず、エラーとして通知します。続く場合は cron の間隔を広げ、手動確認と公式メールで補ってください。
- **`parse` エラー / `short_text`**: ページ構造が変わった可能性があります。`check --show-text` で抽出結果を確認し、`config/targets.yaml` の `parser.content_selector` / `exclude_selectors` を調整してください。`tests/fixtures/pwca_event_real.html` を新しいHTMLに差し替えて pytest も更新してください。
- **誤通知が多い**: 毎回変わる箇所があれば、その要素を `exclude_selectors` に追加します。
- **通知が届かない**: Actionsログの `Telegram send failed: HTTP ...` を確認します。Botをブロックした、Chat IDが違う、トークンを再発行した、などが原因です。

## 監視対象・キーワード・URLの変更

`config/targets.yaml` を編集してpushします。コードの変更は不要です。

- **サイトURL・ディレクトリ**: `site.base_url`（例 `https://pwca.org`）と `site.events_path`（例 `/events/`）を変数として持ち、各対象のURLは `base_url + events_path + slug` で組み立てます。サイトの移転やディレクトリ変更の際はここだけを直します。環境変数 `PWCA_BASE_URL` / `PWCA_EVENTS_PATH` で一時的に上書きもできます。
- **配下ページ**: `subpages.enabled` で有効/無効、`max_per_target` で上限、`exclude_url_patterns` で除外を変更します。slug以外の文字列で配下を判定したいときは、対象に `subpage_scope:` を指定します。
- **大会の追加**: `targets` に `id`・`name`・`slug` を追加します。events ディレクトリ外のページや公式PDFは `url:` で直接指定します。一時停止は `enabled: false` です。サイト全体のクロールはしません。
- **キーワード**: `keywords` に追加・削除します。大文字小文字は区別しません。英語は単語単位で一致し（`room` は `classroom` に一致しない）、ハイフンと空白は同一視します（`check-in` = `check in`）。
- **通知先を増やす**: `notifier.py` の `Notifier` インターフェース（`send(Notification)`）を実装したクラスを追加します。

## 仕様からの変更点とその理由

- **ファイル配置**: 仕様の `pwca-monitor/` サブディレクトリではなく、リポジトリ直下に配置しました。`config.py`・`monitor.py`・`selftest.py` を追加し、設定・本体ループ・自己テストを分離しています。
- **取得失敗時の終了コード**: 403等の取得失敗はTelegramに通知し、ジョブは成功扱い（`::warning::`）にしています。5分ごとに赤いバツと失敗メールが続くのを避けるためです。通知失敗と状態保存失敗はジョブを失敗させます。
- **`check` は常に読み取りのみ**: `--dry-run` は明示用に受け付けます。ベースラインは `monitor` の初回に作成されます。
- **`last_success_at`**: 毎回の成功時刻ではなく、最後に内容を保存した時刻です（5分ごとのコミットを避けるため）。稼働確認には `last_heartbeat_at`（1日1回更新）を使います。
- **選手リスト（Selection List）を除外**: 静的HTMLではスケルトン表示で、実データは遅延読み込みです。宿泊情報とも無関係なため除外しました。
- **schedule の有効化**: ワークフローの編集ではなく、リポジトリ変数 `MONITOR_ENABLED` で切り替える方式にしました。
- **Python**: Actionsでは3.12に固定しています。ローカルでは3.14で検証しました。
