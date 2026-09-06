# HOTAI Smart Home（和泰智慧家）for Home Assistant

把「和泰智慧家」App 裡的和泰／嘉儀 (Karo's) 智慧家電接進 Home Assistant。
目前對應 **除濕機**（TaiSEIA device 4，SA04 / RD / RDI 系列）。

App 背後是 Exosite **ExoHome** 雲端（`karos.apps.exosite.io`），機器用 TaiSEIA 協定透過 Wi-Fi 模組上雲。
這個整合直接走 App 用的同一組 API（REST 登入 + WebSocket 推播），所以是 **cloud push**：
機器狀態改變時雲端會即時推 `device_change`，不用輪詢。

## 安裝

1. HACS → Integrations → ⋮ → Custom repositories → 加入 `https://github.com/dahliaki04/ha-hotai`，類型 Integration。
2. 安裝後重啟 HA。
3. 設定 → 整合 → 新增 → 搜尋「HOTAI」→ 輸入和泰智慧家 App 的 email / 密碼。

## 實體

| 平台 | 實體 | ESH 欄位 |
|---|---|---|
| humidifier | 除濕機（電源、模式、目標濕度、目前濕度） | H00 H01 H03 H07 |
| sensor | 室內溫度、室內濕度、電流、電壓、功因、即時功率、累積用電量、錯誤訊息、側向出風口、雲端裝置狀態 | H06 H07 H19 H1A H1B H1C H1D H12 H0F |
| binary_sensor | 滿水、濾網需清洗、除霜中、雲端連線 | H0A H0B H11 |
| switch | 自動風向、氣氛燈、機體防霉、高濕度提示、按鍵鎖、靜音 | H08 H0C H13 H14 H16 H18 |
| select | 風速、除濕段數、乾衣段數、風向段數、空氣清淨模式、聲音設定 | H0E H04 H05 H09 H0D H10 |
| number | 定時關機（小時）、高濕度提示值 | H02 H15 |

只會建立機器實際回報有支援的欄位（雲端 `fields` 清單）。

模式對應（H01）：0 自動除濕 · 1 設定除濕 · 2 連續除濕 · 3 乾衣 · 4 空氣清淨 · 5 防霉防蟎 · 6 送風 · 7 人體舒適 · 8 低濕乾燥。

## 先用 probe 看看你的機器回什麼

不需要 HA，只要 Python 3.11+ 和 `pip install aiohttp`：

```bash
python tools/probe.py you@example.com 'password'            # 列裝置、印完整紀錄、聽 30 秒事件
python tools/probe.py you@example.com 'password' --set <SN> H00=1   # 開機測試
```

它會把結果寫到 `tools/probe_dump.json`（token 會遮掉）。電力相關欄位（H19–H1D）的單位是依 TaiSEIA 慣例推的，
先用 probe 對照 App 顯示的數值再信。

## 可以 local control 嗎？

不行（目前）。Wi-Fi 模組只在配對時開 `_exosh1._tcp` mDNS 服務和 `192.168.1.1:32051` 的設定用 HTTP，
配對完就只剩對雲端的 MQTT 連線，沒有區網 API。真的要 local，走硬體路線：
機器和 Wi-Fi 模組之間是 UART 的 TaiSEIA 協定，可以換成 ESP32 跑 ESPHome 的 TaiSEIA 元件
（[tsunglung/taixia](https://github.com/tsunglung/taixia)、[xangin/TaiSEIA_ESPhome_samples](https://github.com/xangin/TaiSEIA_ESPhome_samples)）。

## 協定筆記（給想改的人）

- `POST https://karos.apps.exosite.io/api:1/session` `{"email","password"}` → `{"token","id"}`；密碼錯回 HTTP 400 `Auth fail.`
- `wss://karos.apps.exosite.io/api:1/phone`，第一個 frame `{"id":1,"request":"login","data":{"token"}}`（id 必須是整數，字串會回 code 300 Bad request）
- request：`{"id":<int>,"request":<name>,"device":<sn>,"data":{...}}`；名稱有 `get_me` `lst_device` `get` `set` `config` `calendar` `provision_token` `lst_user` `add_user` `rem_user` `set_properties` `del_device` `ota` `get_group` `set_group`…
- event：`device_change`（`data.device` + `data.changes.{status,profile,properties,connected,device_state}`）、`add_device`、`del_device`、`token_expired`
- 欄位定義來自 App 內建的 `assets/models/SA04.yaml`（除濕機）。其他 SA01（冷氣）… SA18 也在 App 裡，要接別的和泰家電照樣加平台即可。
