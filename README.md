# PWCA大会ページ更新監視

PWCA Panchgani大会ページを GitHub Actions で定期取得し、内容が変わったら Telegram に通知します。宿泊・予約・料金などのキーワードを含む変更は「宿泊関連の可能性」として優先通知します。

> 通知は参考情報です。予約開始や空室を保証しません。公式メール・主催者からの連絡も引き続き確認してください。

## 構成

```
.github/workflows/monitor.yml   GitHub Actions（schedule + 手動/外部からの実行）
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

監視対象は2大会で、それぞれ「大会ページ」と「その大会の配下ページ」を監視します。加えて、PWCAニュース一覧から、Panchgani・宿泊関連の記事を通知します。状態保存と通知は、ページごとに分かれています。

処理の流れ: 取得 → 抽出（大会ページは `[data-page-events-show]` 内のみ。カウントダウン・ページ下部の共通ニュース欄・SNS・スポンサー・選手リストは除外。ニュースは一覧ページで別途監視）→ 前回状態と比較 → 分類 → 通知 → 状態保存。

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

## 現在の稼働状況（2026-10-14 時点）

| 項目 | 内容 |
|---|---|
| リポジトリ | https://github.com/nichi-xian/pwca-monitor （Public） |
| 監視対象 | Asian Tour 大会ページ、World Cup 大会ページ、各配下ページ、PWCAニュース（関連記事のみ通知） |
| 起動 | cron-job.org から5分ごと（主）＋ GitHub schedule（予備） |
| 通知先 | Telegram Bot `@nichi_pwca_monitor_bot` |
| 状態の保存先 | ブランチ `monitor-state` の `state.json` |
| 外部起動用トークンの期限 | 2027-03-01 |

```
cron-job.org（5分ごと）──workflow_dispatch──▶ GitHub Actions（monitor）──更新があれば──▶ Telegram
```

## セットアップ手順（実際に行った手順）

作り直すときや、別の大会で同じ仕組みを作るときは、この順番で進めます。**トークン・Chat ID などの値は、チャット・エディタ・ファイル・Issueに貼らないでください。** 保管はメモアプリなどで行い、GitHubにはSecretsとして登録します。

### ステップ1：Telegram Botを作る

1. Telegramで公式アカウント **`@BotFather`** を開き、`/newbot` を送ります。
2. 表示名（例：`ニチPWCA監視`）を送ります。チャット一覧にはこの名前で表示されます。
3. ユーザー名を送ります。英数字と `_` だけを使い、最後を `bot` にします（例：`nichi_pwca_monitor_bot`）。
4. 「Done! Congratulations on your new bot.」と表示され、**Botトークン**（`数字:英数字`）が表示されます。メモアプリに控えます。

- `Sorry, an error has occurred during your request` と出た場合は、BotFather側の一時的なエラーです。`/cancel` を送り、時間をおいて `/newbot` からやり直します。

**トークンを作り直す（漏れた可能性があるとき）**
1. BotFatherに `/revoke` を送ります。
2. 「Choose a bot to generate a new token.」と出たら、`@nichi_pwca_monitor_bot` と**文字で**送ります（ボタンが見えない場合もこれでOK）。
3. 確認が出たら承認します。「Your token was replaced with a new one.」と新しいトークンが表示されます。古いトークンはこの時点で使えなくなります。
4. 別の方法：`/mybots` → Botを選ぶ → 「API Token」 → 「Revoke current token」。
5. GitHubの Secret `TELEGRAM_BOT_TOKEN` も新しい値に更新します（ステップ4）。

### ステップ2：Botとのチャットを開始し、Chat IDを調べる

1. Telegramのチャット一覧に戻り、検索欄（🔍）で `nichi_pwca_monitor_bot` を検索して開きます。BotFatherのチャットとは**別**の、Bot本人とのチャットです。一覧には表示名（`ニチPWCA監視`）で出ます。
2. 「開始（START）」を押し、`hello` などを1通送ります。Botは返信しませんが、それで正常です。この操作をしないと、Botからの通知が届きません。
3. **Chat ID を調べる**：Telegramのチャット一覧の検索欄で `userinfobot` を検索し、**`@userinfobot`** を開いて「開始」を押します。返信の `Id:` の数字が Chat ID です（Botとの1対1チャットでは、Chat ID は自分のユーザーIDと同じです）。
   - 別の方法として、ブラウザで `https://api.telegram.org/bot<トークン>/getUpdates` を開き、`"chat":{"id":` の数字を見る方法もあります。ただし今回は `{"ok":true,"result":[]}` と空のままで取れなかったため、`@userinfobot` を使いました。このURLにはトークンが含まれるので、共有やスクリーンショットはしないでください。

### ステップ3：GitHubリポジトリを作ってアップロードする

1. https://github.com で右上の「＋」→「New repository」を押します。名前を `pwca-monitor`、**Public** にし、README などの追加は**すべてなし**で「Create repository」を押します。
   - Public の理由：Actionsの実行時間が無料・無制限だからです。Privateの無料枠は月2,000分で、5分間隔だと約8,600分/月になり超過します。公開されるのはコードと公開大会ページの内容だけで、Secretsは公開されません。
2. GitHub CLI をインストールしてログインします。
   ```bash
   brew install gh
   gh auth login --hostname github.com --git-protocol https --web
   ```
   表示された8文字のコードを https://github.com/login/device に入力し、「Authorize github」を押します。続けて次を実行します。
   ```bash
   gh auth setup-git
   ```
3. **`workflow` 権限を追加します**（これがないと `.github/workflows/` をpushできません）。
   ```bash
   gh auth refresh -h github.com -s workflow
   ```
   同様にコードを入力して承認します。`gh auth status` の Token scopes に `workflow` があればOKです。
4. **メールアドレスを公開しない設定**にします（Publicリポジトリではコミットのメールアドレスが公開されるため）。このリポジトリだけ、GitHubの非公開用アドレスを使います。
   ```bash
   gh api user --jq .id        # 数字のIDを確認
   git config user.name "nichi-xian"
   git config user.email "<ID>+nichi-xian@users.noreply.github.com"
   ```
   `--global` を付けないので、他のプロジェクトの設定は変わりません。
5. コミットしてアップロードします。
   ```bash
   git init -b main
   git add .
   git status --short    # .env や .venv、state/*.json が含まれていないことを確認
   git commit -m "Add PWCA event page monitor"
   git remote add origin https://github.com/nichi-xian/pwca-monitor.git
   git push -u origin main
   ```

### ステップ4：Secretsを登録する

1. リポジトリの **Settings** → 左メニュー **Secrets and variables** → **Actions** を開きます。
2. 「New repository secret」で次の2つを登録します。値はブラウザに直接入力し、前後にスペースや改行を入れないでください。

| Name | 値 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | ステップ1のBotトークン（作り直した場合は**最新**のもの） |
| `TELEGRAM_CHAT_ID` | ステップ2のChat ID |

値を変更するときは、一覧の鉛筆マーク（✏️）から「Update secret」で更新します。

### ステップ5：GitHub上で手動テストする

Actions タブ → 「PWCA monitor」 → 「Run workflow」で mode を選んで実行します。コマンドで行う場合は `gh workflow run monitor.yml -f mode=<mode>` を使います。1つずつ ✅ を確認してから次に進みます。

| 順番 | mode | 確認すること |
|---|---|---|
| 1 | `self-test` | ✅（pytest・自己テスト・fixtureテストがすべて合格） |
| 2 | `check` | ✅、ログに各ページの `HTTP: 200` と本文の文字数が出る（GitHubのサーバーからPWCAを読めることの確認） |
| 3 | `notify-test` | ✅、Botのチャットに「PWCA監視 テスト通知」が届く |
| 4 | `monitor`（1回目） | ✅、通知なし（初回は記録のみ）。`monitor-state` ブランチが作られる |
| 5 | `monitor`（2回目） | ✅、通知なし。ログが `unchanged=[...]` になる |

- `notify-test` が `HTTP 401: Unauthorized` で失敗したら、Botトークンの誤りです（作り直す前の古いトークンなど）。最新のトークンで `TELEGRAM_BOT_TOKEN` を更新します。
- `HTTP 400: Bad Request: chat not found` なら Chat ID の誤りか、Botとのチャットを開始していません（ステップ2）。

### ステップ6：GitHubの定期実行（schedule）をONにする

Settings → Secrets and variables → Actions → **Variables** タブ → 「New repository variable」で、`MONITOR_ENABLED` = `true` を登録します。

- 今回は、ONにしてから最初の起動まで**約6時間**かかり、その後も**3〜4時間に1回**しか起動しませんでした。そのため、ステップ7の外部起動を主な起動方法にし、schedule は予備として残しています。

### ステップ7：cron-job.org から5分ごとに起動する

**7-1. 起動用のトークンを作る（GitHub）**
1. https://github.com/settings/personal-access-tokens/new を開きます。
2. 次のように設定して「Generate token」を押します。

| 項目 | 設定 |
|---|---|
| Token name | `pwca-cron-trigger` |
| Expiration | Custom → **2027-03-01** |
| Repository access | **Only select repositories** → `nichi-xian/pwca-monitor` |
| Repository permissions | **Actions: Read and write** だけ（他は変更しない。Metadata: Read-only は自動で付く） |

3. 表示された `github_pat_...` は1回しか表示されないので、メモアプリに控えます。このトークンでは、このリポジトリの実行開始しかできません。

**7-2. cron-job.org のアカウントを作る**
1. https://console.cron-job.org/signup で登録します（Time zone は Asia/Tokyo）。
2. 確認メールのリンクを開いて、ログインします。

**7-3. ジョブを作る**
Cronjobs → 「CREATE CRONJOB」で、次のように入力して保存します。

COMMON タブ：

| 項目 | 設定 |
|---|---|
| Title | `PWCA monitor` |
| URL | `https://api.github.com/repos/nichi-xian/pwca-monitor/actions/workflows/monitor.yml/dispatches` |
| Enable job | オン |
| Save responses in job history | オン |
| Execution schedule | **Every 5 minutes** |
| Notifications | 失敗時（execution of the cronjob fails）にチェック |

ADVANCED タブ：

| 項目 | 設定 |
|---|---|
| Request method | **POST** |
| Headers | `Authorization`: `Bearer github_pat_...`（`Bearer`＋半角スペース＋7-1のトークン） |
|  | `Accept`: `application/vnd.github+json` |
|  | `X-GitHub-Api-Version`: `2022-11-28` |
|  | `Content-Type`: `application/json` |
| Request body | `{"ref":"main","inputs":{"mode":"monitor"}}` |

**7-4. 動作確認**
Actions タブで、`workflow_dispatch` の実行が5分おきに並び、✅ になっていればOKです。今回は 2026-10-14 11:40 / 11:45（JST）に起動と成功を確認しました。

## 運用

- **ふだん**：何もしなくて大丈夫です。変化がなければ通知は来ません。
- **確認したいとき**：https://github.com/nichi-xian/pwca-monitor/actions で、✅ が5分おきに並んでいれば正常です。`monitor-state` ブランチの `state.json` の `last_heartbeat_at` は1日1回更新されます。
- **監視を止める**：cron-job.org でジョブの「Enable job」をオフにします。GitHub側の schedule も止めるには、`MONITOR_ENABLED` を `false` にします（これだけでは外部起動は止まりません）。
- **cron-job.org から失敗メールが届いたら**：401 はトークンの期限切れか誤り（7-1で作り直し、Authorization を更新）、404 はURLの誤りです。
- **トークンの期限（2027-03-01）**：大会後も使う場合は、期限前に7-1で作り直して cron-job.org のヘッダーを更新します。
- **Telegramのトークンを作り直したとき**：Secret `TELEGRAM_BOT_TOKEN` を更新し、`notify-test` で確認します。
- **schedule の60日ルール**：Publicリポジトリで60日間活動がないと、GitHubが schedule を無効化することがあります（事前にメールが届きます）。外部起動は影響を受けませんが、予備を残したい場合は Actions 画面で「Enable workflow」を押します。

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

## ニュースの監視

宿泊・ホテルの案内はニュース記事として出る可能性があるため、ニュース一覧（https://pwca.org/news、最新9件）も監視しています（`targets` の `pwca_news`）。大会ページの下にあるニュース欄は、サイト全体の最新3件が表示されているだけで大会専用ではないため、そちらは比較から除外し、一覧ページのほうで見ています。

- 新しい記事は**本文を1回だけ**取得します（`fetch_once`）。既に読んだ記事は再取得しません。
- 記事本文に `notify_only_if_contains` の語（Panchgani、India、hotel、accommodation、booking、宿泊 など）を含む場合**だけ**、「📄 新規ページ」として通知します。宿泊系の語があれば 🏨 になります。
- 他大会のレース報告など、関係のない記事は通知しません。一覧ページそのものの変化（記事が増えた等）も通知しません（`notify_page_changes: false`）。
- 他大会の記事でも「次はインドのPanchgani大会です」といった予告文があれば通知されます。
- すべての記事を通知したい場合は、`notify_only_if_contains` を削除します。

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
- **配下ページの監視を追加**: 大会ページからリンクされた、同じ大会のページも監視します（成績・リザルトは除外）。
- **ニュース一覧の監視を追加**: 宿泊案内がニュース記事として出る可能性があるため、https://pwca.org/news を監視し、Panchgani・India・宿泊関連の語を含む新しい記事だけ通知します。
- **外部からの定期起動を追加**: GitHubの schedule が3〜4時間に1回程度しか起動しなかったため、cron-job.org から5分ごとに `workflow_dispatch` を呼び出す方式を主な起動方法にしました（GitHub schedule は予備）。
