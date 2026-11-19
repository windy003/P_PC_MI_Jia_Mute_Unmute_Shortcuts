# 腾讯应用引擎 · 音量开关

给 Windows 11 加一个全局快捷键，一键给「腾讯应用引擎」（`AndrowsVm.exe`）**静音 / 恢复声音**。
只影响这个应用，不影响 QQ 音乐、浏览器等其他程序。

## 功能

- 一个**可自定义**的全局快捷键（默认 `Ctrl + Alt + M`）：按一次静音，再按一次恢复
- 自动识别腾讯应用引擎的音频进程，也可以在列表里手动指定
- 切换时屏幕下方浮层提示「已静音 / 已恢复声音」
- 可选开机自动启动
- 单文件程序，配合 `pythonw` 运行时不出现黑色控制台窗口

## 环境要求

- Windows 10 / 11
- Python 3.9+（已在 3.13 上测试）
- 依赖：`pycaw`、`comtypes`、`psutil`

## 安装依赖

双击 `安装依赖.bat`，或在命令行执行：

```
pip install pycaw comtypes psutil
```

## 运行

双击 `启动.bat`（不显示控制台窗口），或：

```
pythonw tencent_volume_keys.py
```

首次运行会自动识别腾讯应用引擎并把它设为目标。如果列表里没有它，先启动引擎并让它出点声音（音频会话只在应用发声音时才存在），再点「刷新列表」，选中 `AndrowsVm.exe` 后点「设为快捷键目标」。

## 使用

1. 在「快捷键」一栏点「录制」，按下想要的组合键（必须带 Ctrl / Alt / Shift / Win 之一，例如 `Ctrl+Alt+M`、`Alt+F9`）
2. 点「保存并应用」
3. 之后在任意窗口按这个键，就能给引擎静音 / 恢复声音

「试一下」可以先验证效果；勾选「开机自动启动」后程序会随 Windows 启动并最小化到任务栏。

## 说明与排查

- 音量使用的是 Windows「每个应用独立音量」（系统音量合成器里 `AndrowsVm.exe` 那一列）
- 如果引擎没在播放声音，它的音频会话可能暂时不存在，此时按键会提示找不到目标；播放一下声音即可
- 快捷键被其他程序占用时，「保存并应用」会弹窗提示，换一组组合键即可
- 配置文件：`%APPDATA%\TencentAppVolumeKeys\config.json`
- 日志：`%APPDATA%\TencentAppVolumeKeys\app.log`
- 自检：`python selftest.py`（测试代码单独放在 `selftest.py`，主程序里不包含测试）

## 打包成 exe（可选）

```
pip install pyinstaller
pyinstaller -n 音量开关 -w -F tencent_volume_keys.py
```

生成的 `dist\音量开关.exe` 可以放到任意位置，开机自启会在注册表里引用这个 exe。
