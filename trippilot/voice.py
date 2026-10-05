"""语音链路接口：ASR / TTS。

诚实边界：
- 本脚手架默认提供确定性 stub，保证主循环可跑通。
- 「真实测得」的 WER/CER、延迟等指标，必须在真实录音 + 真实模型上实测，
  由 tests/ 与 eval 显式标注 measured=true；stub 结果绝不充当实测数据。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ASRHypothesis:
    text: str
    confidence: float
    measured: bool = False  # 是否来自真实录音实测


class ASRClient:
    def transcribe(self, audio_path: str) -> ASRHypothesis:
        raise NotImplementedError


class TTSClient:
    def synthesize(self, text: str) -> bytes:
        raise NotImplementedError


class StubASR(ASRClient):
    """确定性 stub：按文件名返回固定转写（测试用，非实测）。"""

    def transcribe(self, audio_path: str) -> ASRHypothesis:
        return ASRHypothesis(
            text="明天下午两点去中关村面试，帮我查路线并提前一小时提醒我",
            confidence=0.95, measured=False)


class StubTTS(TTSClient):
    def synthesize(self, text: str) -> bytes:
        return b""  # stub：不实际合成


# TODO(实测接入点)：FunASR 或 Whisper 的真实 transcribe 实现，
# 测得的 WER/CER 与延迟写入 eval 报告的 measured 部分。
