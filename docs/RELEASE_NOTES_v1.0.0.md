# Ledge v1.0.0 发布说明

Windows 10 / 11 桌面边缘液态毛玻璃灵动岛首个正式发布版本。

## 核心亮点

- **液态灵动岛与 5 套皮肤**：支持屏幕四边任意吸附与拖拽拉断液桥，内置深色毛玻璃、浅色毛玻璃、晴空渐变、胭脂撞色与经典纯黑皮肤，动态 60 FPS / 空闲 30 FPS 自适应节能。
- **时间字体与颜色定制**：内置 7 种精选时间字体风格（简约、数码管、霓虹、衬线、宽体、窄体、圆润）与 8 种配色方案（主题默认色、琥珀、青色、粉色、柠檬绿、跟随封面、自定义颜色、渐变），支持深浅主题自动对比度适配与「收起态显示天气」开关。
- **矢量天气联动**：内置精致线条矢量天气图标，与时间字体和主色柔和同步；展开态支持多城市管理、5 天温差条与逐小时预报。
- **邮件与验证码卡片**：支持多账号 IMAP IDLE 监听，展开详情自动缓存纯文本；收到验证码邮件时在左侧弹出 180 秒大字预览与一键复制。
- **AI 额度监控**：支持 DeepSeek、WorkBuddy、Codex、Gemini、Grok Bot，凭据全部存放于 Windows 凭据管理器。
- **实时双语字幕**：支持系统声音/麦克风捕获与本地离线日语识别 + 中文实时字幕翻译。
- **工具、生活提醒与系统维护**：文件置物架、文字剪贴板历史、待办/喝水/久坐提醒、设置页一键检查 GitHub 更新及脱敏诊断包导出。

## 便携版安装与升级

- **全新安装**：下载 `Ledge-portable.zip` 解压至任意目录，确保 `Ledge.exe` 与 `_internal` 同级，双击 `Ledge.exe` 启动。
- **从旧版升级**：退出当前运行的 Ledge 后直接覆盖解压新版本文件；原有 `config.json`、系统凭据与 `data/` 本地数据会自动兼容保留。
- **彻底卸载**：关闭设置中的“开机自启”（或清理注册表开机启动项）、在 Windows 凭据管理器移除 `wisland:*` 凭据后，直接删除程序目录即可。

## 文件校验（SHA-256）

- `Ledge-portable.zip`（89,379,206 字节）
  ```text
  33189a5a23f2fa432343aa619feba0fadd28894178d19ca5bfba6fffe5f3464b
  ```
  验证命令（PowerShell）：
  ```powershell
  Get-FileHash .\Ledge-portable.zip -Algorithm SHA256
  ```

## 已知限制

- 尚未经过 Windows 10 实机回归验证；混合 DPI 多显示器与长时间驻留仍在持续验证中。

## 致谢

- 时间字体风格、配色方案与开源字体选型参考了 [MoFeng2223/BrimDeck](https://github.com/MoFeng2223/BrimDeck)（`Orbitron`、`Instrument Serif`、`Unbounded`、`Bebas Neue`、`Fredoka`，SIL OFL 1.1）。
- 产品功能与交互参考了 [Renly1994/xiaotang-daozai](https://github.com/Renly1994/xiaotang-daozai)，基于 [AsamiAkito/wisland](https://github.com/AsamiAkito/wisland) 演进重构。
