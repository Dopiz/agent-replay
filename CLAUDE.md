# 專案:agent-replay — Claude Code session 回放工具

## 目標
建立一個 Python CLI 工具,透過 Claude Code hooks 記錄所有 tool call,
並產出單檔靜態 HTML 時間軸回放。開源專案,README 需英文。

## 技術約束
- Python 3.11+,用 uv 管理,打包成可 `uvx agent-replay` 執行的 CLI
- CLI framework 用 typer
- HTML 產生用 Jinja2 template,產出必須是「單一 HTML 檔」
  (CSS/JS 全部 inline,不依賴 CDN,可離線開啟、可直接分享)
- hook script 必須極輕量、絕不能拋出未捕捉的例外
  (任何錯誤都靜默寫入 log,不能干擾 Claude Code 本身運作)

## 功能需求

### 1. `agent-replay init`
- 讀取 `~/.claude/settings.json`,注入 hooks 設定
  (PreToolUse、PostToolUse、UserPromptSubmit、SessionStart、SessionEnd)
- hooks 指向本套件內的 hook handler(用 `uvx agent-replay hook <event>` 形式)
- 若 settings.json 已有其他 hooks,必須合併而非覆蓋
- 執行前先備份原始 settings.json

### 2. Hook handler(`agent-replay hook <event>`)
- 從 stdin 讀 Claude Code 傳入的 JSON payload
- 寫入 `~/.agent-replay/sessions/{session_id}.jsonl`,每行一個 event:
  {ts, session_id, event_type, tool_name, tool_input, tool_output,
   duration_ms(PostToolUse 時計算), cwd, error(若有)}
- tool_input/output 超過 50KB 時截斷並標記 truncated: true

### 3. `agent-replay list`
- 列出已記錄的 sessions:session_id 縮寫、開始時間、專案目錄、
  event 數量、是否有失敗

### 4. `agent-replay open [session_id]`
- 不給 session_id 時預設開最新一筆
- 解析 JSONL,產出 HTML 到 `~/.agent-replay/reports/{session_id}.html`
  並用預設瀏覽器開啟
- HTML 需求:
  - 垂直時間軸,每個 event 一張卡片,依時間排序
  - UserPromptSubmit 顯示為醒目的「章節分隔」
  - tool call 卡片:tool 名稱、耗時、成功/失敗(失敗紅色標記)
  - 卡片可點擊展開完整 input/output(JSON pretty print,長內容可捲動)
  - 頂部 summary bar:總時長、tool call 數、失敗數、各 tool 使用次數
  - 支援按 tool name 篩選
  - 深色主題,乾淨現代,不要花俏動畫

### 5. SessionEnd 自動產出
- SessionEnd hook 觸發時自動產出 HTML 並印出檔案路徑
- 可透過 `~/.agent-replay/config.toml` 關閉(auto_report = false)

## 專案結構建議
agent_replay/
  cli.py          # typer entry point
  hooks.py        # hook handler
  parser.py       # JSONL -> session model
  render.py       # Jinja2 -> HTML
  templates/report.html.j2
tests/            # pytest,重點測 parser 和 hooks 合併邏輯

## 開發順序
1. 先做 hooks.py + init(用假 payload 測試)
2. 再做 parser + render(先用手寫的假 JSONL 驗證 HTML 效果)
3. 最後串起來 end-to-end 測試
4. 寫 README:含安裝、init、open 的使用說明與截圖佔位

## 驗收標準
- 在真實 Claude Code session 跑完後,`agent-replay open` 能看到
  完整時間軸
- hook 註冊後,Claude Code 正常運作無任何感知差異
- HTML 單檔 < 2MB(一般 session),離線可開
