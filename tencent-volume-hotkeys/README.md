# 腾讯应用引擎 · 音量开关

给 Windows 11 加一组全局快捷键，给「腾讯应用引擎」（`AndrowsVm.exe`）**静音 / 恢复声音**、**加减音量**。
只影响这个应用，不影响 QQ 音乐、浏览器等其他程序。

## 功能

- 三个**可自定义**的全局快捷键：
  - 音量开关（默认 `Ctrl + Alt + M`）：按一次静音，再按一次恢复
  - 音量 +（默认 `Ctrl + Alt + ↑`）：每次提高 5%
  - 音量 −（默认 `Ctrl + Alt + ↓`）：每次降低 5%
- 自动识别腾讯应用引擎的音频进程
- 触发时屏幕下方浮层提示「已静音 / 已恢复声音 / 音量 45%」
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

双击 `run_MiJia_Mute_App.cmd`（或其快捷方式），程序会**直接最小化到系统托盘**，不弹主窗口；也可以在命令行运行：

```
pythonw tencent_volume_keys.py --minimized   # 同上：启动后只留托盘图标
pythonw tencent_volume_keys.py               # 需要看主窗口时用这个
```

## 使用

1. 在「快捷键」一栏，对「音量开关 / 音量 + / 音量 −」分别点「录制」，按下想要的组合键（必须带 Ctrl / Alt / Shift / Win 之一，例如 `Ctrl+Alt+M`、`Alt+F9`）
2. 录制完会立即保存并生效，不需要再点其他按钮
3. 之后在任意窗口按这些键，就能给引擎静音 / 恢复声音、加减音量

音量键不会自动解除静音；如果当前处于静音状态，提示会显示「音量 45% · 静音中」。三个快捷键不能重复，重复时会弹窗提示。

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
