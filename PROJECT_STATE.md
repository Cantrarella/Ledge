# Wisland 项目状态与交接说明

> 最后更新：2026-11-19  
> 项目目录：`D:\Codex\wisland`  
> 用途：新会话恢复上下文、防止重复规划、记录已完成功能、未完成事项和下一步计划。  
> 维护规则：每个阶段完成、提交或出现重要决策后，必须更新本文件。

## 1. 新会话启动规则

接手本项目时，先执行：

```powershell
Get-Content D:\Codex\wisland\PROJECT_STATE.md
git -C D:\Codex\wisland status --short --branch
git -C D:\Codex\wisland log -6 --oneline --decorate
```

然后以当前源码和 Git 状态为准核对本文件。

必须遵守：

1. 不重新规划本文件标记为“已完成”的功能。
2. 不重复输出已经在对应阶段交付过的完整指令。
3. 新改动前保留工作区中已有的未提交修改。
4. 未经用户明确要求，不执行 `git reset --hard`、`git checkout --` 或覆盖工作区。
5. 未经用户确认，不提交或推送 GitHub。
6. 阶段完成后，将提交哈希、测试结果、遗留风险和下一阶段写回本文件。

## 2. 当前仓库快照

当前主分支：

```text
main
```

最近已推送提交：

```text
e08b06a fix(live): harden capture shutdown and credential handling
8d86ed0 fix(notifications,ui): restore hidden clients safely and tune spacing
2da15be (HEAD -> main, origin/main, origin/HEAD) fix(ui,clipboard): center weather/toast layout and fix 64-bit clipboard ctypes
cf43611 feat: finalize quota providers, themes, and life reminders
f670261 feat(quota): complete AI provider integrations and release cleanup
97c9bdc feat(quota): add in-island AI quota page and themed AI SVG icons
807ca38 feat(quota): add quota service foundation, credentials, DeepSeek adapter and settings page
e9aa703 feat(notify,ui): add themed unread quick dock, foreground auto-clear and align header gear
```

当前阶段已收尾完成（首页 `419×140` 紧凑布局与单行标题适配、收起态时间天气居中、天气页 `419×232` 网格重构、7 个设置分类合并、Tooltip 主题适配及托盘收起修复）。

## 3. 产品总体方向

- 对外显示品牌：**Ledge**（主窗口/设置窗口/关于页/托盘菜单/Tooltip/`Ledge.exe`/`Ledge.vbs`/`setup.bat`）。
- GitHub 仓库：
  - `origin`：`https://github.com/Cantrarella/Ledge.git`（已通过 GitHub 原生重命名保留全部历史与 Fork 关系）
  - `upstream`：`https://github.com/AsamiAkito/wisland.git`（保持不变）
- 内部技术标识：`APP_ID = "wisland"`，保留 Python 包名 `wisland`、本地目录 `D:\Codex\wisland`、配置路径 `config.json`、数据目录 `data/`、日志路径 `data/wisland.log`、单实例锁 `data/wisland.lock`、凭据前缀 `Wisland/mail/*` 与 `Wisland/quota/*`、剪贴板格式 `Wisland.ClipboardHistoryCopy`、IMAP ID 与 HTTP User-Agent 等兼容协议，确保零数据迁移风险。
- 开机启动由注册表 `HKCU\...\Run\wisland` 安全迁移至 `HKCU\...\Run\Ledge`（先验证目标存在并写入新项成功后再删旧项；兼容入口 `wisland.vbs` 静默转发至 `Ledge.vbs`）。
- `README.md` 已按当前真实功能与 9 张全新 Ledge 截图全量重做。
- 最终产品以 Ledge（原 Wisland）为主壳。
- 岛体、小表情、弹簧动画、液桥、贴边和鼠标交互以现有主体为准。
- Daozai 只提供业务功能、信息结构和必要的逻辑参考。
- 不移植 Daozai 的小形象、egui UI 或岛体动画。
- 不恢复已废弃的 Xland 主线。

## 4. 收起态规则

- 收起态只轮换两种岛：
  1. Wisland 原版小表情岛。
  2. Wisland 原版时间天气岛。
- 默认每 30 秒轮换。
- 侧边停靠时隐藏小表情。
- 不给 AI 额度新增第三种收起态。
- 未读状态使用 Wisland 原版小色点。
- 页面底部圆点不再叠加消息红点。

## 5. 最终页面与尺寸

最终一级页面：

1. 首页 `home`
2. 邮件 `mail`
3. AI 额度 `quota`
4. 天气 `weather`
5. 实时字幕 `trans`
6. 工具 `tools`
7. 生活 `life`

消息页 `msg` 已删除，不得恢复。

主要面板尺寸：

| 页面 | 尺寸 |
|---|---:|
| 首页 | 419×140 |
| 邮件列表 | 419×140 |
| 邮件详情 | 419×236 |
| AI 额度 | 419×176 |
| 天气 | 419×232 |
| 实时字幕 | 419×185 |
| 工具 | 419×140 |
| 生活 | 419×140 |

首页已完成 `419×140` 紧凑双栏布局（左侧 `MediaCard` + 48×48 静态封面 + 单行自适应 `NowPlayingLine`；右侧 126px 小形象区）。

## 6. 已完成功能

### 6.1 基础与设置

- 独立 Qt 设置窗口。
- 左侧分类、右侧卡片、统一保存/取消。
- 配置草稿、校验、原子写入、未知字段保留。
- 页面开关、排序和自由拖拽。
- 主题、启动、岛体、天气、通知、邮件、字幕、工具生活、数据、关于等设置。

### 6.2 岛体与交互

- Wisland 小表情、弹簧、液桥、贴边和鼠标交互保留。
- 自适应帧率：动态约 60 FPS，空闲约 30 FPS。
- `WheelScrollArea.wheelEvent()` 已修复：有滚动余量且未到边界时内部消费 (`accept`)，无余量或到达边界时调用 `e.ignore()` 冒泡给岛体触发翻页。

### 6.3 通知

- Windows 通知读取与权限处理。
- Windows 10/11 兼容路径。
- QQ、微信、邮件通知。
- QQ/微信图标固定使用现有 SVG 原始颜色。
- QQ/微信图标可见区域 22×22px，槽位 24×24px。
- 时间天气在通知存在时仍保持居中，未读色点固定在右侧。
- 通知去重、队列、免打扰、摘要和任务栏闪烁兼容。
- 点击 QQ/微信通知后聚焦客户端。
- 点击邮件通知后进入邮件页并打开对应邮件。

### 6.4 岛旁未读快捷区

- 仅 QQ 和微信进入聊天未读快捷区，邮件和其他系统通知不进入。
- 收起态仍显示 Wisland 小色点。
- 展开态单个未读显示裸图标，无圆形底。
- 两个以上未读共用 40px 宽竖向中性玻璃胶囊。
- 图标 22px，保持原始品牌色。
- 不显示右上角红点和数字角标。
- 单个约 40×40，两项约 40×76，三项约 40×112。
- 超过三项内部滚动，不切页。
- 悬停采用聚焦式：图标轻微放大、悬停行中性高亮、其他项变淡。
- 清除后使用 Spring 做轻微回弹，剩余图标平滑补位。
- 用户直接切到 QQ/微信前台时自动清除对应来源未读。
- 打开失败不提前清除，并播放轻微失败晃动。

### 6.5 邮件

- 多账号 IMAP。
- Windows Credential Manager 保存授权码。
- 邮件列表、账号筛选、全部/未读、全部已读。
- 邮件详情、安全纯文本正文、内部滚动。
- 最近 20 封 Header，最近 5 封同步预取正文，其余 15 封后台预取。
- 本地正文缓存与 UIDVALIDITY 隔离。
- IMAP IDLE，失败降级轮询。
- 首次监听建立 UID baseline，不回放旧邮件。
- 新邮件通知。
- 左侧大验证码预览卡。
- 验证码显示 180 秒，过期后回到普通邮件列表。
- 不读取附件。
- 不做写信、回复、转发和附件处理，且后续也不计划增加。

### 6.6 工具与生活

- 工具页：文件置物架、文字剪贴板。
- 生活页：待办、喝水、久坐提醒。
- 不包含日程。

### 6.7 实时字幕

- 日语识别、中文译本。
- 收起态支持最新字幕显示。
- 音频设备断开或采集异常时自动停止流水线，不再让界面永久停在“正在聆听”。
- 退出程序时等待流水线线程安全结束；单句原始音频缓存随 2~60 秒最大句长配置扩展。
- 实时字幕 API Key 使用 Windows 凭据管理器 `Wisland/translate/deepseek`，旧版 `config.json` 明文 Key 首次读取自动迁移并清除。
- 本地 `dist/Ledge` 开发目录优先复用源码根目录 `models/`，避免约 630MB 模型重复下载。
- 翻译失败缓存限制为 128 条；HTTP 429 遵守 `Retry-After`（默认退避 2 秒）；迟到草稿按 `L1/L2...` 句号丢弃，不能覆盖新句。
- 完整模型文件增加最低尺寸校验，避免空文件或损坏文件被误判为可用。
- 回归：`tests/test_live_pipeline.py` 7/7 通过，相关设置/页面测试 10/10 通过；全量测试首次 92 passed / 8 failed，其中 2 项因剪贴板残留敏感内容导致，清空后单独复跑 2/2 通过；剩余 6 项均为受限桌面环境的注册表、凭据管理器或固定截图目录问题。新包已用 PyInstaller 重建并启动验证。
- 不包含翻译页、词典、生词本和 Anki。

### 6.8 AI 额度基础服务与设置页

已完成并推送：

- `QuotaAccount`、`QuotaSnapshot`、`QuotaWindow`、`QuotaBalance` 数据模型。
- Windows Credential Manager 存储额度凭据。
- 本地额度缓存。
- 异步 `QuotaManager`。
- 自动刷新、并发防重、429 退避、旧快照保留。
- 设置页“AI 额度”分类。
- 真实 DeepSeek API 余额查询。
- API Key 不进入 `config.json`、缓存或日志。

服务商范围（当前五服务商已全部完成适配器接入，`implemented=True`）：

- DeepSeek：已接通（API Key 余额/充值/赠送/多币种查询）。
- WorkBuddy：已接通（本机登录只读检测 + 默认浏览器官网授权轮询 + Token 续期 + 积分与资源包解析）。
- Codex：已接通（只读 `$CODEX_HOME/auth.json` / `~/.codex/auth.json` + `wham/usage` 主/额外周期窗口解析）。
- Gemini：已接通（Google 邮箱 + 会话 Cookie + 同站 `executeQuery` GraphQL 双池解析）。
- Grok Bot：已接通（PKCE 浏览器授权轮询 + Token 续期 + `GetSandUsageStatus` 周额度解析）。
- DSH：永久排除。

## 7. 当前阶段（已完成）

### 视觉与交互收尾（首页、天气、设置与托盘）

- 阶段：视觉与交互收尾（首页 419×140、收起态时间天气、天气页 419×232 网格、设置页 7 分类、Tooltip 主题适配、托盘切换修复）
- 状态：完成
- 提交：cc5ffbf（基于 e65f3a8，完成凭据真机闭环、QMouseEvent 弃用消除、多显示器热插拔/睡眠唤醒恢复及发布矩阵验证）
- 首页布局：`419×140`（`height=98`），移除旧 745px 宽布局、首页标题与右侧快捷操作；封面固定 `48×48` 无 hover 缩放/发光；采用单行 `NowPlayingLine` 同行左对齐连续显示标题与歌手并自左向右省略；右侧小形象区 `126px`（表情 `~54–60px`）。
- 收起态时间天气：`CompactView._idle_layout_metrics` 基于 `QFontMetrics` 将时间与天气作为整体严格水平居中（间距 `9px`，无竖线/圆点/胶囊，使用等宽数字避免负数或三位温度跳动）。
- 天气页网格：`419×232` 保持不变，页头城市按钮仅显示城市名 + 独立 `+` 按钮；左上当前天气改用单一 `QGridLayout`（52px 左列图标+温度，右列左对齐描述/温差/体感且隐藏时不跳动）；底部 `Facts` 改用固定 4 列（两行 `x` 坐标完全一致）。
- 设置页与 Tooltip：11 个一级分类合并为 7 个（`外观与岛体`、`页面与导航`、`消息与邮件`、`天气`、`AI 与字幕`、`工具与生活`、`数据与关于`），保留全部原有设置项、深链、唯一“取消 / 保存设置”及凭据提交顺序；`SettingsWindow` 改用 `QApplication` 级带标记的 `QToolTip` QSS 规则随 `settings_theme` 切换并在关闭时恢复原样式。
- 托盘修复：`populate_tray_menu` 使用 `lambda _checked=False: island.toggle()` 消除 `QAction.triggered(bool)` 误传 `False` 导致“收起 Wisland”无效果的问题。
- 微信未读聚焦修复：通知点击改用 `WindowService.activate()`；可见/最小化窗口正常聚焦，隐藏到托盘的主窗口通过配置的显示窗口热键恢复，禁止直接 `ShowWindow(SW_SHOW)`。客户端候选增加 `WS_EX_NOACTIVATE` 过滤和 200×200 最小面积约束，并优先选择面积最大的可见主窗口；通知专项测试 13/13 通过。
- 收起态间距微调：时间与天气间距由 9px 调整为 14px；微信/QQ 通知左侧内距由 9px 调整为 18px，两类通知共用同一布局，关闭按钮位置保持不变。收起态、通知和未读快捷区专项测试 16/16 通过。
- 真实凭据闭环与稳定性增强：
  - 在交互式 Windows 会话中使用独立合成测试键通过真实 Windows Credential Manager 闭环验证（小凭据写入/读取/覆盖/删除、>8KB 大 JSON 分块保存与完整还原、分块与小凭据互转自动清理旧分块）；`WindowsQuotaCredentialStore._read_single` 与 `QuotaManager` 明确区分底层凭据读取错误（如 `WinError 1312`）与会话失效，保留旧有效快照为 `STALE`。
  - `tests/test_home_page.py` 改用 `QtTest.QTest.mouseClick`，消除全部 `QMouseEvent` 弃用警告。
  - `Island` 接入 `screenAdded` / `screenRemoved` / `primaryScreenChanged` / 屏幕几何变化以及 `WM_POWERBROADCAST`、`WM_DISPLAYCHANGE`、`WM_DPICHANGED`，支持显示器热插拔后停靠位置恢复、清空旧毛玻璃帧防黑屏，以及睡眠唤醒后重置帧时钟并自动触发天气/AI 额度/邮件监听后台刷新。
- 测试结果：`tests/test_home_page.py` + `tests/test_release_matrix.py` `11 passed`（**0 警告**），全量测试 `89 passed`；`python -m compileall -q wisland tests` 与 `git diff --check` 通过。
- 打包与隔离目录发布验证：PyInstaller 6.22.3 使用 `WIsland.spec` 构建成功；将发布包复制到源码外全新隔离目录并清空 `PYTHONPATH` 后，验证包内资源（图标、5 个 `ai-icons`、4 个 `life-icons`、5 个额度适配器、`sherpa_onnx`、`config.example.json`）、首次启动、旧配置升级、损坏配置保护及开机启动注册表读写往返均通过。
- 邮件滚动、详情收起与 Gemini 登录修复：
  - `WheelScrollArea.scrollContentsBy` 禁用透明窗口内 `viewport->scroll` 脏像素搬运，改用 `inner.move(-ox, -oy)` 并同步刷新顶层透明区域，彻底消除邮件列表垂直重影拖尾。
  - `MailPage.on_hide` 在岛体收起或切走页面时自动从邮件详情（`419×236`）返回邮件列表（`419×140`）。
  - `connect_gemini_browser` 改以本机 CDP `/json/list` 页面存活为准，修复启动器进程提前退出误报“浏览器窗口已关闭”的问题，并支持登录后停留在 `/app` 等页面的会话与邮箱提取；全量测试 `tests/` `97 passed`（0 failed）。
- 修改文件：`wisland/ui/widgets.py`、`wisland/ui/pages/mail.py`、`wisland/services/quota/adapters/gemini.py`、`tests/test_mail_page.py`、`tests/test_quota_foundation.py`、`PROJECT_STATE.md`
- 已知风险：
  1. 实机 8 小时 / 24 小时连续驻留及真实硬件睡眠唤醒、多物理显示器热插拔仍需在物理桌面周期性观察。
- 下一阶段：
  1. Win10/Win11、高 DPI、多显示器、睡眠唤醒、断网重连和长期运行稳定性测试。
  2. 版本号、更新检查、崩溃日志、诊断信息和正式发布包。

## 8. 后续计划

1. Win10/Win11、高 DPI、多显示器、睡眠唤醒、断网重连和长期运行稳定性测试。
2. 版本号、更新检查、崩溃日志、诊断信息和正式发布包。

## 9. 已知问题与待验证项

### 打包目录与用户数据

- `Ledge.exe` 按设计使用自身所在目录下的 `config.json` 与 `data/`；根目录版本与 `dist/Ledge/Ledge.exe` 是两套独立数据目录，切换启动位置不会自动共享设置。
- `scripts/build.ps1` 在重建前备份已有 `dist/Ledge/config.json` 与 `data/`，先生成不含个人数据的便携压缩包，再仅向本地 `dist/Ledge` 恢复原状态。
- 排查“账号消失”时先确认实际启动的是哪一个 `Ledge.exe`，不要直接重新录入账号或覆盖配置。

### QQ 未读打开

- 只有一个 QQ 主客户端时，旧通知绑定的 PID 可能已经失效。
- 当前应优先使用精确 PID/窗口；失效后重新枚举当前 QQ。
- 只有一个当前主客户端时应允许打开；多个真实客户端才保留歧义。
- `notify.open.qq` 配置路径应作为 QQ 未运行时的启动入口。
- 群聊和私聊不会创建新的 QQ 客户端账号，不应被误判为多实例。

### 微信未读打开

- 已修复点击微信未读快捷区时直接强制显示隐藏托盘窗口导致的界面卡死风险。
- 隐藏窗口当前通过 `apps.restore_hotkey.weixin.exe`（默认 `Ctrl+Alt+W`）恢复。
- 仍需在真实微信正常、最小化、托盘隐藏和异常退出状态下做最终实机复核。

### 未读前台清除

- 仅客户端进入前台后清除，不允许后台运行误清。
- QQ/微信多实例和无法确认账号时保留未读。
- 非标准改名客户端通过精确路径映射处理。

### AI 额度

- WorkBuddy、Codex、Gemini、Grok Bot 均已通过本机合成服务器/测试替身完成闭环测试；真实线上账号（特别是 WorkBuddy 403 修复后的实机连接、Codex/Gemini/Grok Bot 真实账号）需由用户实机验证。
- 完整测试集需注意环境限制：受限会话无法写入 Windows Credential Manager，系统 Python 未安装 `psutil`（需加 `PYTHONPATH=D:\Codex\wisland;D:\Codex\wisland-exe\build\site-packages`）。
- 根目录遗留临时 pytest 目录（`.pytest-tmp-*`）可在提交前清理，不要把临时目录提交到仓库。

### 安全与稳定

- 实时字幕 API Key 已迁移到 Windows 凭据管理器，`config.json` 不再保留明文 Key。
- 前台 WinEventHook 需要声明正确的 64 位 ctypes 签名，并在退出时释放。
- 测试中的 PySide6 `QMouseEvent` 弃用警告建议改用 QTest。

## 10. 永久排除

- Daozai 小形象。
- Daozai egui UI 和岛体动画。
- Xland 主线。
- 消息页 `msg`。
- 日程和日历。
- 写信、回复、转发邮件和附件处理。
- 翻译页、词典、生词本和 Anki。
- DSH。

## 11. 新窗口开场提示

可直接发送：

```text
请先读取 D:\Codex\wisland\PROJECT_STATE.md 和 D:\Codex\wisland\AGENTS.md，
然后核对 git status 和 git log。

以 PROJECT_STATE.md 为项目事实来源：
- 不要重新规划已完成阶段
- 不要重复已经交付过的完整指令
- 保留现有未提交修改
- 继续当前工作和下一步，不要从零开始
```

## 12. 阶段更新模板

每次阶段完成后，在本文件追加或更新：

```text
阶段：AI 额度、主题拆分、经典皮肤、生活提醒
状态：完成
提交：待本次提交后记录
测试：全量测试 tests/ 72/72 通过；compileall 与 git diff --check 通过
打包：PyInstaller 6.22.3 构建 WIsland.spec 通过，包内资源与 EXE 启动冒烟正常
修改文件：PROJECT_STATE.md、app.py、core/config.py、ui/theme.py、ui/widgets.py、ui/pages/system.py、ui/settings_window.py、ui/settings/*、ui/island.py、ui/compact.py、ui/icons.py、assets/life-icons/*、tests/test_config_and_settings.py
已知风险：受限测试环境未写入真实 Windows Credential Manager；真实线上 AI 账号与长期运行稳定性待实机持续观察
下一阶段：1. 首页最终布局确认；2. Win10/Win11、高 DPI、多显示器、睡眠唤醒、断网重连和长时间稳定性测试；3. 版本号、崩溃日志、诊断信息和正式发布包
```

```text
阶段：v1.0.3 发布 + 内存/渲染/歌词磁盘缓存与前奏过滤优化（2026-11-21）
状态：完成
已发布基线：v1.0.3 (b6a3347) — 酷狗 KRC 逐字卡拉 OK 歌词、原生矢量字号 1.08x 微弹上浮、长歌词单向滚动、60s 非会员试听时长兼容
本轮待提交优化：
1. 内存与帧率：LiveService 改为 SubtitlePage.on_show() 时按需 preload()，实机常驻私有提交内存由 607.2 MB 降至 80.6 MB；CompactView.busy() 在音乐暂停且波形/切行过渡完成后让出 60FPS；热路径字体、度量与整行/逐字宽度缓存复用。
2. 歌词体验：_CREDIT 正则放宽至支持带空格的中英文制作人员标签（如“和声编写 Harmonic Arrangement:”），抽取 _clean_leading_rows；新增 DATA_DIR/lyrics_cache.json 本地磁盘缓存（上限 200 首、TTL 7 天、原子写入）。
3. 测试收尾：修复 tests/test_notifications.py 最大宽度上限测试样本，全量 pytest 116/116 通过。
修改文件：PROJECT_STATE.md、wisland/live/service.py、wisland/ui/pages/subtitle.py、wisland/ui/compact.py、wisland/services/lyrics.py、tests/test_notifications.py
下一阶段：1. 视需要打包发布 v1.0.4（或先提交优化改动到 main）；2. 展开态音乐卡是否同步 KRC 逐字扫色
```

```text
阶段：v1.0.13 发布 — 展开态星群拓扑聚光灯与五套皮肤光学适配（2026-11-27）
状态：完成
功能与优化：
1. 展开态鼠标跟随聚光灯：仅在展开态且内容层显现后激活，包含三层光学结构（① 岛面漫射底光、② 原有实体卡片内表面柔光与 1.3px 探照流光边 + 高不透明皮肤星群透射补偿、③ 岛体外轮廓 1.7px 流光描边），以及 1:1 像素晶格星群拓扑（32 颗悬浮节点、95px 三角网格连线、115px 光标牵引线，跨页面高度不缩水）。
2. 五套皮肤定制配光：深色毛玻璃（冷白微蓝）、经典纯黑（压低底光防发灰 + 0.88 卡片星群透射）、胭脂撞色（#FF7CA2 霓虹柔粉）、浅色毛玻璃与晴空渐变（深海宝石蓝星点 + 冰晶蓝光场 + 单调连续钴蓝探照边框，消除浅底白点隐形与双色断层）。
3. 常规设置项与审查加固：在「外观与岛体 -> 常规」新增「展开态聚光灯」开关（island.spotlight_enabled）、「聚光效果类型」选择器（island.spotlight_style：星群拓扑 / 纯净柔光）及「聚光半径大小」（island.spotlight_radius：80~260 px，默认 155 px）；修复展开初段 Veil 面纱同步渐显、工具页剪贴板嵌套 Card 双层同心框抑制及窗口原点平移坐标同步；鼠标静止 1s 后自动停帧归零 CPU 占用。
测试：全量 pytest 196/196 通过。
修改文件：wisland/core/branding.py、build/version_info.txt、wisland/core/config.py、wisland/ui/theme.py、wisland/ui/island.py、wisland/ui/widgets.py、wisland/ui/pages/tools.py、wisland/ui/settings/general.py、tests/test_island_modules.py、tests/test_release_readiness.py、PROJECT_STATE.md
```

### v1.0.13 发布后聚光修复与打包瘦身（2026-11-27）

- 两处聚光修复已完成，基于发布提交 `30f37d7`。
- 聚光半径先检查原始数值范围，再转整数。加载异常配置时回退到 155 px。
- 岛面和卡片共用由动画帧推进的星群相位。鼠标停止后，局部重绘保留星群位置，鼠标再次移动时从原相位继续。
- 清理 `wisland/ui/island.py` 废弃空方法与未启用定时器，并在 `Ledge.spec` 中进一步剔除未链接的 `sherpa-onnx-c/cxx-api.dll`、未使用的 `numpy.random` / `numpy.fft` / `yaml` / `charset_normalizer` / `click` / `setuptools` 及 `.dist-info` / `comtypes/test` 冗余文件。
- 本地全量回归测试 198/198 通过（含 4 个参数子用例）。
- 实体多屏、高 DPI 和桌面重绘仍待实机复核。
