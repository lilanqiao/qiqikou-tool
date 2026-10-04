"""去气口核心（新版）：Silero VAD 判断人声 + 频谱规则抓换气 + 原有压低/缩短处理。

和旧版的区别只在"哪里是停顿"的判断：
  旧版：只看音量阈值 → 响的换气被当成说话，轻的字被当成停顿
  新版：AI 判断是不是人声（Silero VAD），再用频谱规则（参考 De-Breather）
        把夹在字之间、VAD 没切开的短换气也找出来
处理方式（压低 + 渐变 + 只缩超长停顿）沿用旧版，讲话部分一个采样都不改。

音频读写用 soundfile（自带 libsndfile，原生支持 WAV/MP3/FLAC），不依赖 ffmpeg，
Windows / macOS 打包都不用额外带外部程序。
"""
import os
import sys
import numpy as np
import onnxruntime as ort
import soundfile as sf
import soxr

# 打包后资源在 sys._MEIPASS（PyInstaller 的 _internal 目录），开发时在本文件旁边
HERE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "silero_vad.onnx")

SR = 16000
WIN = 512          # Silero v5: 16k 下每帧 512 采样 = 32ms
CTX = 64           # v5 需要带上前一帧末尾 64 个采样作上下文
FRAME_MS = WIN * 1000 / SR

DUCK_DB = 18.0     # 换气/停顿压低多少
RAMP = 40          # ms，压低时的渐变，保证接缝处音量连续
XF = 60            # ms，缩短长停顿时在静音内部做交叉淡化
PAD_MS = 20        # 停顿两端各让出一点给字头字尾（压低时还有 40ms 渐变兜底）


def _vad_probs(mono16k):
    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    state = np.zeros((2, 1, 128), dtype=np.float32)
    ctx = np.zeros((1, CTX), dtype=np.float32)
    sr = np.array(SR, dtype=np.int64)
    n = len(mono16k) // WIN
    probs = np.empty(n, dtype=np.float32)
    for i in range(n):
        chunk = mono16k[i * WIN:(i + 1) * WIN][None, :]
        x = np.concatenate([ctx, chunk], axis=1)
        out, state = sess.run(None, {"input": x, "state": state, "sr": sr})
        probs[i] = out[0, 0]
        ctx = chunk[:, -CTX:]
    return probs


def _band_db(spec, freqs, lo, hi):
    m = (freqs >= lo) & (freqs < hi)
    return 10 * np.log10(spec[:, m].sum(axis=1) + 1e-10)


HOP = 160          # 10ms 一帧做精细判断
NFFT = 512


def _frame_features(mono16k):
    pad = np.concatenate([np.zeros(NFFT // 2, np.float32), mono16k, np.zeros(NFFT // 2, np.float32)])
    frames = np.lib.stride_tricks.sliding_window_view(pad, NFFT)[::HOP]
    spec = np.abs(np.fft.rfft(frames * np.hanning(NFFT), axis=1)) ** 2
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    return {
        "voiced": _band_db(spec, freqs, 80, 400),     # 基音：元音、浊辅音
        "hiss": _band_db(spec, freqs, 3000, 8000),    # 齿音：s/sh/x/c/f
        "total": _band_db(spec, freqs, 80, 8000),
        "periodic": _periodicity(mono16k),
    }


def _periodicity(mono16k, win=640):
    """每 10ms 一个值：40ms 窗口内的归一化自相关峰（基音 70–400Hz 范围）。
    声带振动发出的字有周期（接近 1），气流声没有（0.2–0.45）。"""
    pad = np.concatenate([np.zeros(win // 2, np.float32), mono16k, np.zeros(win // 2, np.float32)])
    frames = np.lib.stride_tricks.sliding_window_view(pad, win)[::HOP]
    frames = frames - frames.mean(axis=1, keepdims=True)
    X = np.fft.rfft(frames * np.hanning(win), n=2 * win, axis=1)
    r = np.fft.irfft(np.abs(X) ** 2, axis=1)[:, :win]
    r = r / (r[:, :1] + 1e-12)
    per = r[:, SR // 400:SR // 70].max(axis=1)
    k = np.ones(5) / 5                       # 50ms 平滑，避免单帧抖动
    return np.convolve(per, k, mode="same")


def _runs(flags):
    out, st = [], None
    for i, f in enumerate(np.append(flags, False)):
        if f and st is None:
            st = i
        elif not f and st is not None:
            out.append([st, i]); st = None
    return out


def detect(data, sr, log=print):
    """返回人声区间 [[start_ms, end_ms], ...]（10ms 精度），区间之外按停顿/换气处理。

    两层判断：
      1. Silero VAD 划出大致的人声区域（抗噪、认得出轻声的字）
      2. 逐 10ms 看频谱（De-Breather 思路）：有基音或有齿音的才是字；
         两样都弱的是换气或静音 —— 即使 VAD 把它算进了人声区域也会被挖出来
    """
    mono = data.mean(axis=1) if data.ndim == 2 else data
    mono = soxr.resample(mono, sr, SR).astype(np.float32) if sr != SR else mono.astype(np.float32)
    probs = _vad_probs(mono)
    ft = _frame_features(mono)
    n = len(ft["voiced"])
    # VAD 是 32ms 一格，放大到 10ms 一格；两边各扩一格，弥补它起止反应慢半拍
    idx = np.minimum((np.arange(n) * 10 / FRAME_MS).astype(int), len(probs) - 1)
    p10 = probs[idx] if len(probs) else np.zeros(n)
    p10 = np.maximum.reduce([p10, np.roll(p10, 3), np.roll(p10, -3)])

    vad_speech = p10 >= 0.5
    if not vad_speech.any():
        return []
    # 以 VAD 判为人声的帧做参考：正常说话时基音带/齿音带有多响
    ref_v = float(np.percentile(ft["voiced"][vad_speech], 50))
    ref_h = float(np.percentile(ft["hiss"][vad_speech], 50))

    voiced = ft["voiced"] > ref_v - 22          # 有像样的基音 = 在发元音/浊音
    hiss = ft["hiss"] > ref_h - 12              # 有像样的齿音 = 在发 s/sh/x 之类
    # 是字：VAD 认为是人声且（有基音或有齿音）；或者基音很强（VAD 漏判的轻声字头）
    word = (vad_speech & (voiced | hiss)) | (ft["voiced"] > ref_v - 12)

    # 第三层：VAD 当成人声、其实是吸气的（常见于下一句开口前那口气）
    # 条件全满足才算：没周期 + 比正常说话低 14dB 以上 + 嘶声弱于齿音 + 连同相邻静音持续 ≥150ms
    ref_t = float(np.percentile(ft["total"][vad_speech], 50))
    rel = ft["total"] - ref_t
    airy = ((ft["periodic"] < 0.5) & (rel < -14) & (ft["hiss"] < ref_h - 2)) | ~word
    n_inhale = 0
    for s, e in _runs(airy):
        if e - s >= 15 and word[s:e].sum() >= 5:   # 里面至少 50ms 原本被当成字，才算新挖出的吸气
            word[s:e] = False; n_inhale += 1

    # 紧贴着字、不到 150ms 的嘶声当作齿音辅音（s/x/sh 的字头字尾），保留；
    # 更长的嘶声才可能是吸气，交给后面当停顿处理
    for s, e in _runs(hiss & ~word):
        touches = (s > 0 and word[max(0, s - 2):s].any()) or (e < n and word[e:e + 2].any())
        if e - s < 15 and touches:
            word[s:e] = True

    # 把字之间 <100ms 的小缝合上（字与字之间的自然衔接，不算停顿）
    for s, e in _runs(~word):
        if e - s < 10 and s > 0 and e < n:
            word[s:e] = True
    # 太短的孤立"字"（<60ms）多半是咂嘴、碰麦，当作停顿
    for s, e in _runs(word):
        if e - s < 6:
            word[s:e] = False

    gaps = [g for g in _runs(~word) if g[1] - g[0] >= 10]
    n_vad_gap = sum(1 for s, e in gaps if p10[s:e].mean() < 0.5)
    log(f"  检测到 {len(gaps)} 处停顿/换气（其中 {len(gaps) - n_vad_gap} 处是夹在句中的换气，{n_inhale} 处是开口前的吸气）")

    total_ms = len(data) * 1000 // sr
    out, cur = [], 0
    for s, e in gaps:
        if s == 0:                       # 开头的静音：整段当停顿，不留边距
            cur = max(cur, e * 10 - PAD_MS); continue
        gs, ge = s * 10 + PAD_MS, e * 10 - PAD_MS   # 两端各让出一点给字头字尾
        if ge - gs < 60:
            continue
        out.append([cur, gs]); cur = ge
    out.append([cur, total_ms])
    return [[int(max(0, s)), int(min(total_ms, e))] for s, e in out if e > s]


class _Clip:
    """按毫秒切片的小工具，行为对齐旧版 pydub：线性幅度渐变、线性交叉淡化。"""

    def __init__(self, data, sr):
        self.d = data; self.sr = sr

    def s(self, ms):
        return int(round(ms * self.sr / 1000))

    def __len__(self):
        return len(self.d) * 1000 // self.sr

    def cut(self, a, b):
        return self.d[self.s(a):self.s(b)]


def _ramp(n, g0, g1):
    return (g0 + (g1 - g0) * np.arange(n) / max(n, 1))[:, None].astype(np.float32)


def _duck(seg, sr):
    g = np.float32(10 ** (-DUCK_DB / 20))
    n = len(seg); r = int(round(RAMP * sr / 1000))
    if n <= 2 * r:
        return seg * g if n > int(sr * 0.004) else seg
    out = seg * g
    out[:r] = seg[:r] * _ramp(r, 1.0, g)
    out[n - r:] = seg[n - r:] * _ramp(r, g, 1.0)
    return out


def _shorten(seg, keep_ms, sr):
    n = len(seg); keep = int(round(keep_ms * sr / 1000))
    if n <= keep:
        return seg
    half = keep // 2
    left = seg[:half]; right = seg[n - (keep - half):]
    xf = min(int(XF * sr / 1000), half - 1, (keep - half) - 1)
    if xf <= int(sr / 1000):
        return np.concatenate([left, right])
    mix = left[-xf:] * _ramp(xf, 1.0, 0.0) + right[:xf] * _ramp(xf, 0.0, 1.0)
    return np.concatenate([left[:-xf], mix, right[xf:]])


def render(data, sr, ranges, max_keep_ms, log=print):
    c = _Clip(data, sr)
    trimmed = 0
    parts = []

    # 首尾的静音不直接裁掉，跟中间停顿一样压低，避免误伤第一个/最后一个字
    parts.append(_duck(c.cut(max(0, ranges[0][0] - 150), ranges[0][0]), sr))
    parts.append(c.cut(*ranges[0]))
    for i in range(1, len(ranges)):
        pe = ranges[i - 1][1]; s, e = ranges[i]
        g = _duck(c.cut(pe, s), sr)
        if (s - pe) > max_keep_ms:
            trimmed += (s - pe) - max_keep_ms
            g = _shorten(g, max_keep_ms, sr)
        parts.append(g)
        parts.append(c.cut(s, e))
    tail_len = len(c) - ranges[-1][1]
    if tail_len > 0:
        parts.append(_duck(c.cut(ranges[-1][1], ranges[-1][1] + min(tail_len, 300)), sr))
    out = np.concatenate(parts)
    f = int(sr * 0.005)
    out[:f] *= _ramp(f, 0.0, 1.0); out[-f:] *= _ramp(f, 1.0, 0.0)
    log(f"  缩掉超长停顿 {trimmed / 1000:.1f}s（换气压低 {DUCK_DB:.0f}dB）")
    return out


SUPPORTED = (".wav", ".mp3", ".flac")


def process(input_path, max_keep_ms=450, out_dir="", log=print):
    if not os.path.exists(input_path):
        log(f"Error: 文件不存在 {input_path}"); return None
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in SUPPORTED:
        log(f"Error: 不支持 {ext}，请用 WAV / MP3 / FLAC"); return None
    log(f"处理：{os.path.basename(input_path)}")
    info = sf.info(input_path)
    data, sr = sf.read(input_path, dtype="float32", always_2d=True)
    orig = len(data) / sr
    log(f"  原长 {orig:.1f}s")

    ranges = detect(data, sr, log)
    if not ranges:
        log("  没检测到人声，保持原样。"); return None
    result = np.clip(render(data, sr, ranges, max_keep_ms, log), -1.0, 1.0)

    base = os.path.splitext(os.path.basename(input_path))[0]
    folder = out_dir.strip() if out_dir and out_dir.strip() else os.path.dirname(input_path)
    os.makedirs(folder, exist_ok=True)
    out_path = os.path.join(folder, base + "_去气口" + ext)
    if ext == ".mp3":
        # 固定码率 192k（libsndfile 用 0~1 表示压缩程度，实测 0.4 = 192k）
        sf.write(out_path, result, sr, format="MP3", bitrate_mode="CONSTANT", compression_level=0.4)
    else:
        sf.write(out_path, result, sr, subtype=info.subtype)
    new = len(result) / sr
    log(f"  完成 Done：{orig:.1f}s -> {new:.1f}s（省 {orig - new:.1f}s）")
    log(f"  已保存 Saved：{out_path}")
    return out_path


if __name__ == "__main__":
    inp = sys.argv[1] if len(sys.argv) > 1 else ""
    keep = int(sys.argv[2]) if len(sys.argv) > 2 else 450
    od = sys.argv[3] if len(sys.argv) > 3 else ""
    if inp:
        process(inp, keep, od)
