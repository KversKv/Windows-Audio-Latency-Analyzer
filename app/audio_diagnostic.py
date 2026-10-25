import argparse
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace

import numpy as np

from app.config import AppConfig, ConfigManager
from audio.audio_backend import AudioBackendError, SoundDeviceBackend, create_audio_backend
from audio.audio_device_manager import AudioDeviceManager
from audio.background_generator import BackgroundGenerator
from audio.capture_engine import CaptureEngine
from audio.playback_engine import PlaybackEngine, ScheduledStimulus
from audio.stream_common import AudioStreamError, StreamTiming

logger = logging.getLogger(__name__)


class AudioLinkReport:
    def __init__(
        self,
        *,
        output_timing: StreamTiming,
        input_timing: StreamTiming,
        captured_frames: int,
        playback_open_count: int,
        playback_start_count: int,
        capture_open_count: int,
        capture_start_count: int,
        stimulus: ScheduledStimulus,
        warmup_seconds: float,
    ) -> None:
        self.output_timing = output_timing
        self.input_timing = input_timing
        self.captured_frames = captured_frames
        self.playback_open_count = playback_open_count
        self.playback_start_count = playback_start_count
        self.capture_open_count = capture_open_count
        self.capture_start_count = capture_start_count
        self.stimulus = stimulus
        self.warmup_seconds = warmup_seconds


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="连续播放/采集音频链路诊断（不测量声学延迟）")
    parser.add_argument("--list-devices", action="store_true", help="列出真实 PortAudio 设备后退出")
    parser.add_argument("--output-device", type=int, default=None, help="播放设备 PortAudio 索引")
    parser.add_argument("--input-device", type=int, default=None, help="录音设备 PortAudio 索引")
    parser.add_argument("--sample-rate", type=int, default=None, help="覆盖配置中的采样率")
    parser.add_argument("--warmup-seconds", type=float, default=None, help="覆盖配置中的预热秒数（1–15）")
    parser.add_argument("--tail-seconds", type=float, default=0.25, help="探针结束后的继续采集时长")
    parser.add_argument("--probe-amplitude", type=float, default=0.05, help="诊断探针峰值幅度")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="每阶段有界等待超时")
    return parser


def _list_devices() -> None:
    inventory = AudioDeviceManager(SoundDeviceBackend()).scan()
    for device in (*inventory.output_devices, *inventory.input_devices):
        logger.info(
            "ID=%s | %s | %s | 输入=%s 输出=%s | 默认采样率=%g",
            device.identifier,
            device.name,
            device.host_api,
            device.max_input_channels,
            device.max_output_channels,
            device.default_sample_rate,
        )


def _wait_for(
    predicate: Callable[[], bool],
    *,
    playback: PlaybackEngine,
    capture: CaptureEngine,
    timeout_seconds: float,
    description: str,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    delay = 0.01
    while not predicate():
        playback.check_health()
        capture.check_health()
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待{description}超时（{timeout_seconds:g} 秒）")
        time.sleep(delay)
        delay = min(0.05, delay * 1.5)
    playback.check_health()
    capture.check_health()


def _make_probe(sample_rate: int, channels: int, amplitude: float) -> np.ndarray:
    frames = max(1, round(sample_rate * 0.01))
    position = np.arange(frames, dtype=np.float64) / frames
    phase = np.arange(frames, dtype=np.float64) / sample_rate
    window = np.sin(np.pi * position) ** 2
    mono = (amplitude * window * np.sin(2 * np.pi * 1000 * phase)).astype(np.float32)
    return np.repeat(mono[:, np.newaxis], channels, axis=1)


def run_diagnostic(
    config: AppConfig,
    *,
    tail_seconds: float = 0.25,
    probe_amplitude: float = 0.05,
    timeout_seconds: float = 30.0,
) -> AudioLinkReport:
    if config.audio.backend != "sounddevice":
        raise AudioBackendError("实时链路诊断需要 sounddevice 后端，离线模拟设备不产生实时流")
    if not 0 < tail_seconds <= 5 or timeout_seconds <= 0:
        raise ValueError("诊断尾音或超时参数无效")
    if not 0 < probe_amplitude + config.audio.background_amplitude <= 1:
        raise ValueError("诊断探针与背景音叠加可能削波")

    sample_rate = config.audio.sample_rate
    warmup_seconds = config.measurement.warmup_seconds
    backend = create_audio_backend(config.audio.backend, sample_rate)
    background = BackgroundGenerator(
        sample_rate=sample_rate,
        channels=config.audio.output_channels,
        amplitude=config.audio.background_amplitude,
        kind=config.audio.background_kind,
    )
    playback = PlaybackEngine(
        backend,
        device_id=config.audio.output_device_id,
        sample_rate=sample_rate,
        channels=config.audio.output_channels,
        blocksize=config.audio.blocksize,
        background=background,
        warmup_seconds=warmup_seconds,
    )
    buffer_seconds = max(
        config.audio.capture_buffer_seconds,
        warmup_seconds + tail_seconds + 2.0,
    )
    capture = CaptureEngine(
        backend,
        device_id=config.audio.input_device_id,
        sample_rate=sample_rate,
        channels=config.audio.input_channels,
        blocksize=config.audio.blocksize,
        capacity_frames=round(sample_rate * buffer_seconds),
    )

    try:
        playback.open()
        capture.open()
        playback.start()
        capture.start()
        _wait_for(
            lambda: playback.is_warmed_up,
            playback=playback,
            capture=capture,
            timeout_seconds=timeout_seconds,
            description="连续流预热",
        )

        lead_frames = max(config.audio.blocksize * 4, round(sample_rate * 0.1))
        stimulus = playback.schedule_stimulus(
            _make_probe(sample_rate, config.audio.output_channels, probe_amplitude),
            lead_frames=lead_frames,
        )
        tail_target = stimulus.end_sample + round(sample_rate * tail_seconds)
        _wait_for(
            lambda: stimulus.state == "completed" and playback.frames_rendered >= tail_target,
            playback=playback,
            capture=capture,
            timeout_seconds=timeout_seconds,
            description="探针调度与尾音采集",
        )
        playback.stop()
        capture.stop()
        captured = capture.read_all()
        return AudioLinkReport(
            output_timing=playback.latest_timing,
            input_timing=capture.latest_timing,
            captured_frames=captured.shape[0],
            playback_open_count=playback.open_count,
            playback_start_count=playback.start_count,
            capture_open_count=capture.open_count,
            capture_start_count=capture.start_count,
            stimulus=stimulus,
            warmup_seconds=warmup_seconds,
        )
    finally:
        try:
            capture.close()
        finally:
            playback.close()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s", force=True)
    args = _build_argument_parser().parse_args(argv)
    try:
        if args.list_devices:
            _list_devices()
            return 0
        config = ConfigManager().load()
        if args.output_device is not None:
            config = replace(config, audio=replace(config.audio, output_device_id=args.output_device))
        if args.input_device is not None:
            config = replace(config, audio=replace(config.audio, input_device_id=args.input_device))
        if args.sample_rate is not None:
            config = replace(config, audio=replace(config.audio, sample_rate=args.sample_rate))
        if args.warmup_seconds is not None:
            config = replace(
                config,
                measurement=replace(config.measurement, warmup_seconds=args.warmup_seconds),
            )
        report = run_diagnostic(
            config,
            tail_seconds=args.tail_seconds,
            probe_amplitude=args.probe_amplitude,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception:
        logger.error("音频链路诊断失败", exc_info=True)
        return 1

    logger.info(
        "诊断通过 | 播放=%s 帧/%s 回调 | 采集=%s 帧/%s 回调 | 缓冲读取=%s 帧",
        report.output_timing.sample_count,
        report.output_timing.callback_count,
        report.input_timing.sample_count,
        report.input_timing.callback_count,
        report.captured_frames,
    )
    logger.info(
        "流生命周期 | 播放打开/启动=%s/%s | 采集打开/启动=%s/%s | 预热=%g s",
        report.playback_open_count,
        report.playback_start_count,
        report.capture_open_count,
        report.capture_start_count,
        report.warmup_seconds,
    )
    logger.info(
        "探针 | 起始样本=%s | 状态=%s | 估计 DAC 播放时间=%s",
        report.stimulus.start_sample,
        report.stimulus.state,
        report.stimulus.playback_time_seconds,
    )
    logger.info(
        "最新流时间戳 | 输出 DAC=%s | 输入 ADC=%s",
        report.output_timing.output_dac_time,
        report.input_timing.input_adc_time,
    )
    logger.info("诊断仅验证连续流、调度和采集，不计算声学延迟，也不保证消除蓝牙省电行为。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
