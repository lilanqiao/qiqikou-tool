# 去气口工具

口播配音自动去换气、剪长停顿。讲话部分一个字都不改，语速不变。

- **换气 / 开口前的吸气**：压低 18dB，带渐变，不硬切
- **超长停顿**：缩短到设定长度（默认 450ms）
- 支持 WAV / MP3 / FLAC，Windows 10/11 和 macOS 11+，不联网、不需要显卡

## 下载
到右侧 **Releases** 下载对应系统的安装包。

## 原理
1. [Silero VAD](https://github.com/snakers4/silero-vad) 划出大致的人声区域
2. 逐 10ms 看频谱：有基音（80–400Hz）或齿音（3–8kHz）的才是字，思路参考 [De-Breather](https://github.com/MeanTemperature/DeBreather-for-Audacity)
3. 用周期性（自相关）找出 VAD 漏判的「开口前吸气」

## 开发
```
pip install numpy onnxruntime soundfile soxr tkinterdnd2 pyinstaller
python app.py                       # 运行界面
python qiqikou_core.py 音频.mp3 450  # 命令行处理
pyinstaller qiqikou.spec            # 打包
```
推送到 main 后，GitHub Actions 会自动打包 Windows / Mac 版并更新 Releases。
