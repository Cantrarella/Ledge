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
97c9bdc (HEAD -> main, origin/main, origin/HEAD) feat(quota): add in-island AI quota page and themed AI SVG icons
807ca38 feat(quota): add quota service foundation, credentials, DeepSeek adapter and settings page
e9aa703 feat(notify,ui): add themed unread quick dock, foreground auto-clear and align header gear
7ae0d2f feat(mail,settings): remove msg page, always show OTP for 180s, fix mail list render and polish settings UI
6c1c1dc feat(mail): add mail list/detail UI, OTP preview card, body cache, and IDLE monitor
```

当前工作区已整理并准备提交“AI 额度完整接入、滚轮冒泡修复与收尾修复（WorkBuddy SVG 裁切、大凭据分块与保存无竞态、进度条 Tooltip 移除、Daozai 风格设置页与 Gemini 浏览器登录）”：

```text
已纳入本次提交范围：
- AGENTS.md, PROJECT_STATE.md
- wisland/app.py, wisland/ui/page_registry.py, wisland/ui/widgets.py, wisland/ui/icons.py
- wisland/assets/ai-icons/workbuddy.svg
- wisland/ui/pages/quota.py, wisland/ui/settings/quota.py, wisland/ui/settings_window.py
- wisland/services/quota/credentials.py, wisland/services/quota/manager.py, wisland/services/quota/providers.py
- wisland/services/quota/adapters/__init__.py, workbuddy.py, codex.py, gemini.py, grok_bot.py
- tests/test_quota_foundation.py, tests/test_quota_settings.py, tests/test_quota_page.py
```

## 3. 产品总体方向

- 最终产品以 Wisland 为主壳。
- 岛体、小表情、弹簧动画、液桥、贴边和鼠标交互以 Wisland 为准。
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
| 邮件列表 | 419×140 |
| 邮件详情 | 419×236 |
| AI 额度 | 419×176 |
| 天气 | 419×232 |
| 实时字幕 | 419×185 |
| 工具 | 419×140 |
| 生活 | 419×140 |

首页仍保留 Wisland 原布局，是否压缩或重排尚未最终决定。

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

## 7. 当前阶段（已完成，待验收与提交）

### AI 额度重构与五服务商接入（阶段 1–6）

目标：

- 移除 `QuotaPage` 灰色 `Card` 与垂直滚动条，内容直接落在 419×176 透明岛体表面。
- 实现 `quota` 页面动态显隐（`pages.visible.quota AND has_connected_accounts()`），无真实已连接账号时隐藏且删除最后一个账号后安全切页。
- 按服务商区分连接流程并逐个接入 WorkBuddy、Codex、Gemini、Grok Bot。
- 同步修复 WorkBuddy 请求头（浏览器 `User-Agent` / `Origin` / 无 `X-Client-Platform` 回退）及设置页连接按钮宽度截断。

验收结果：

- 额度专项测试 25/25 通过（`tests/test_quota_page.py`、`tests/test_quota_settings.py`、`tests/test_quota_foundation.py`）。
- 相关回归测试 29/30 通过（`tests/test_mail_page.py`、`tests/test_config_and_settings.py`、`tests/test_unread_dock.py`）；全库测试 67/68 通过（唯一失败项为既有 `TestConfigAndSettings.test_15_life_todo_water_sit_and_reminders` 硬编码日期 `2026-11-18` 在 `2026-11-19` 跨天触发清零，与本次改动无关）。
- `python -m compileall -q wisland tests` 与 `git diff --check` 通过。
- 浅色 (`light`)、深色 (`dark`)、晴空 (`sky`)、胭脂 (`rouge`)、自动深色 (`auto-dark`)、自动浅色 (`auto-light`) 419×176 截图检查通过（WorkBuddy 图标无残影、无黑色 Tooltip 遮挡）。
- 已于 `2026-11-19 07:32:51` 通过 PyInstaller 6.22.3 重新打包并同步 `D:\Codex\wisland\WIsland.exe` 与 `_internal`，确认 5 个 AI SVG 图标与 4 个新 adapter 模块均已打入。

状态：已完成审查、清理并准备提交推送到 `origin/main`。

## 8. 后续计划

按顺序执行：

1. 已验收岛内 AI 额度页。
2. 已补齐 `quota` 的页面注册、默认配置、设置页 fallback 和相关测试。
3. 已检查图标资源进入 PyInstaller 包；待有构建环境时重新产出 EXE 并做启动验收。
4. 待用户明确授权后，提交并推送 AI 额度展示层。
5. 逐个接入剩余服务商：
   - WorkBuddy
   - Codex
   - Gemini
   - Grok Bot
6. 重做或确认首页最终布局。
7. 最终稳定性测试：
   - Win10 / Win11
   - 高 DPI
   - 多显示器
   - 睡眠唤醒
   - 断网重连
   - 长时间运行
   - 设置窗口反复开关
   - Qt 崩溃和黑屏
8. 版本号、更新检查、崩溃日志、诊断信息和发布包。

## 9. 已知问题与待验证项

### QQ 未读打开

- 只有一个 QQ 主客户端时，旧通知绑定的 PID 可能已经失效。
- 当前应优先使用精确 PID/窗口；失效后重新枚举当前 QQ。
- 只有一个当前主客户端时应允许打开；多个真实客户端才保留歧义。
- `notify.open.qq` 配置路径应作为 QQ 未运行时的启动入口。
- 群聊和私聊不会创建新的 QQ 客户端账号，不应被误判为多实例。

### 未读前台清除

- 仅客户端进入前台后清除，不允许后台运行误清。
- QQ/微信多实例和无法确认账号时保留未读。
- 非标准改名客户端通过精确路径映射处理。

### AI 额度

- WorkBuddy、Codex、Gemini、Grok Bot 均已通过本机合成服务器/测试替身完成闭环测试；真实线上账号（特别是 WorkBuddy 403 修复后的实机连接、Codex/Gemini/Grok Bot 真实账号）需由用户实机验证。
- 完整测试集需注意环境限制：受限会话无法写入 Windows Credential Manager，系统 Python 未安装 `psutil`（需加 `PYTHONPATH=D:\Codex\wisland;D:\Codex\wisland-exe\build\site-packages`）。
- 根目录遗留临时 pytest 目录（`.pytest-tmp-*`）可在提交前清理，不要把临时目录提交到仓库。

### 安全与稳定

- 实时字幕 API Key 仍保存在 `config.json`，建议后续迁移到 Windows 凭据管理器。
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
阶段：AI 额度重构、五服务商接入、滚轮冒泡修复与收尾发布准备
状态：已完成审查、清理与打包验证，准备提交推送 origin/main
提交：e7dd39c（含本轮审查修复与收尾清理）
测试：额度专项 25/25 通过；全库 67/68 通过（唯一失败为无关测试 test_15 硬编码日期跨天）；compileall 与 git diff --check 通过
验证环境：Windows / Python 3.13.14 / PySide6；light/dark/sky/rouge/auto-dark/auto-light 419×176 实际渲染检查；PyInstaller 6.22.3 打包通过
修改文件：AGENTS.md、PROJECT_STATE.md、app.py、page_registry.py、ui/widgets.py、ui/icons.py、assets/ai-icons/workbuddy.svg、pages/quota.py、settings/quota.py、settings_window.py、services/quota/credentials.py、providers.py、manager.py、adapters/* 与额度测试
遗留问题：Gemini 浏览器自动采集与 WorkBuddy/Grok Bot/Codex 真实线上账号端到端行为待用户在最新 EXE 中进一步实机确认；test_15 日期硬编码待另行修正
下一阶段：提交并推送 origin/main 后，转入首页最终布局或发布稳定性工作
```
