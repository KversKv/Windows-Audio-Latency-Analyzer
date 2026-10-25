import numpy as np
import pytest

from audio.audio_backend import (
    AudioBackend,
    AudioCallbackTime,
    AudioStream,
    AudioStreamStatus,
)
from audio.background_generator import BackgroundGenerator
from audio.capture_engine import CaptureEngine
from audio.playback_engine import PlaybackEngine
from audio.ring_buffer import AudioRingBuffer
from audio.stream_common import AudioStreamError


class ZeroBackground:
    def __init__(self, sample_rate: int, channels: int) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.peak_amplitude = 0.0

    def render(self, outdata: np.ndarray) -> None:
        outdata.fill(0)


class FakeStream(AudioStream):
    def __init__(self, callback) -> None:
        self.callback = callback
        self.active = False
        self.closed = False
        self.start_count = 0
        self.stop_count = 0

    @property
    def is_active(self) -> bool:
        return self.active and not self.closed

    @property
    def is_closed(self) -> bool:
        return self.closed

    @property
    def callback_error(self) -> Exception | None:
        return None

    def start(self) -> None:
        self.active = True
        self.start_count += 1

    def stop(self) -> None:
        self.active = False
        self.stop_count += 1

    def close(self) -> None:
        self.active = False
        self.closed = True


class FakeBackend(AudioBackend):
    def __init__(self) -> None:
        self.output_stream: FakeStream | None = None
        self.input_stream: FakeStream | None = None
        self.output_parameters = None
        self.input_parameters = None

    def list_devices(self):
        return []

    def open_output_stream(self, **kwargs) -> AudioStream:
        self.output_parameters = kwargs
        self.output_stream = FakeStream(kwargs["callback"])
        return self.output_stream

    def open_input_stream(self, **kwargs) -> AudioStream:
        self.input_parameters = kwargs
        self.input_stream = FakeStream(kwargs["callback"])
        return self.input_stream


def make_playback(backend: FakeBackend, sample_rate: int = 8000) -> PlaybackEngine:
    engine = PlaybackEngine(
        backend,
        device_id=1,
        sample_rate=sample_rate,
        channels=1,
        blocksize=128,
        background=ZeroBackground(sample_rate, 1),
        warmup_seconds=1.0,
    )
    engine.open()
    engine.start()
    return engine


def make_capture(backend: FakeBackend, sample_rate: int = 8000) -> CaptureEngine:
    engine = CaptureEngine(
        backend,
        device_id=2,
        sample_rate=sample_rate,
        channels=1,
        blocksize=128,
        capacity_frames=sample_rate * 2,
    )
    engine.open()
    engine.start()
    return engine


def push_output(
    backend: FakeBackend,
    engine: PlaybackEngine,
    frames: int,
    status: AudioStreamStatus | None = None,
) -> np.ndarray:
    assert backend.output_stream is not None
    outdata = np.empty((frames, 1), dtype=np.float32)
    dac_time = 100.0 + engine.frames_rendered / engine.sample_rate
    time_info = AudioCallbackTime(
        current_time=dac_time - 0.005,
        output_buffer_dac_time=dac_time,
    )
    backend.output_stream.callback(
        outdata,
        frames,
        time_info,
        status or AudioStreamStatus(),
    )
    return outdata


def push_input(
    backend: FakeBackend,
    samples: np.ndarray,
    status: AudioStreamStatus | None = None,
) -> None:
    assert backend.input_stream is not None
    frames = samples.shape[0]
    backend.input_stream.callback(
        samples,
        frames,
        AudioCallbackTime(current_time=200.0, input_buffer_adc_time=199.9),
        status or AudioStreamStatus(),
    )


def finish_warmup(backend: FakeBackend, engine: PlaybackEngine) -> None:
    while not engine.is_warmed_up:
        push_output(backend, engine, 256)


@pytest.mark.parametrize("kind", ["band_limited", "pink"])
def test_background_is_deterministic_band_limited_and_continuous(kind):
    first = BackgroundGenerator(
        sample_rate=8000,
        channels=2,
        amplitude=0.01,
        kind=kind,
        duration_seconds=0.01,
        seed=42,
    )
    second = BackgroundGenerator(
        sample_rate=8000,
        channels=2,
        amplitude=0.01,
        kind=kind,
        duration_seconds=0.01,
        seed=42,
    )
    first_output = np.empty((first.buffer_frames + 7, 2), dtype=np.float32)
    second_output = np.empty_like(first_output)

    first.render(first_output)
    second.render(second_output)

    np.testing.assert_array_equal(first_output, second_output)
    assert np.max(np.abs(first_output)) == pytest.approx(0.01, rel=1e-6)
    assert np.count_nonzero(first_output) > 0
    np.testing.assert_array_equal(first_output[:, 0], first_output[:, 1])
    np.testing.assert_array_equal(first_output[:7], first_output[-7:])


def test_ring_buffer_wraps_and_reports_overwritten_frames():
    ring = AudioRingBuffer(capacity_frames=4, channels=1)
    ring.write(np.array([[0], [1], [2]], dtype=np.float32))
    np.testing.assert_array_equal(ring.read(2).ravel(), [0, 1])

    ring.write(np.array([[3], [4], [5], [6]], dtype=np.float32))

    assert ring.overwritten_frames == 1
    np.testing.assert_array_equal(ring.read_all().ravel(), [3, 4, 5, 6])
    ring.write(np.arange(8, dtype=np.float32)[:, np.newaxis])
    assert ring.overwritten_frames == 1
    assert ring.skipped_frames == 4
    assert ring.dropped_frames == 5
    np.testing.assert_array_equal(ring.read_all().ravel(), [4, 5, 6, 7])


def test_playback_and_capture_open_independently():
    backend = FakeBackend()
    playback = make_playback(backend)
    capture = make_capture(backend)

    assert backend.output_parameters["device_id"] == 1
    assert backend.input_parameters["device_id"] == 2
    assert playback.open_count == capture.open_count == 1
    assert playback.start_count == capture.start_count == 1
    push_output(backend, playback, 128)
    samples = np.arange(64, dtype=np.float32)[:, np.newaxis]
    push_input(backend, samples)
    assert playback.frames_rendered == 128
    assert capture.frames_captured == 64
    np.testing.assert_array_equal(capture.read_all(), samples)

    playback.close()
    capture.close()
    assert backend.output_stream.is_closed
    assert backend.input_stream.is_closed


def test_stimulus_uses_absolute_sample_index_without_restarting_stream():
    backend = FakeBackend()
    engine = make_playback(backend)
    early_stimulus = np.ones((8, 1), dtype=np.float32) * 0.1
    with pytest.raises(AudioStreamError, match="预热"):
        engine.schedule_stimulus(early_stimulus, start_sample=0)
    finish_warmup(backend, engine)

    start_sample = engine.frames_rendered + 32
    scheduled = engine.schedule_stimulus(early_stimulus, start_sample=start_sample)
    output = push_output(backend, engine, 128)

    assert scheduled.state == "completed"
    assert scheduled.playback_time_seconds == pytest.approx(100.0 + start_sample / 8000)
    assert np.count_nonzero(output[:32]) == 0
    np.testing.assert_allclose(output[32:40], 0.1)
    assert np.count_nonzero(output[40:]) == 0
    assert engine.latest_timing.sample_index == start_sample - 32
    assert engine.latest_timing.output_dac_time == pytest.approx(100.0 + (start_sample - 32) / 8000)

    second_start = engine.frames_rendered + 16
    second = engine.schedule_stimulus(early_stimulus, start_sample=second_start)
    second_output = push_output(backend, engine, 64)
    assert second.state == "completed"
    np.testing.assert_allclose(second_output[16:24], 0.1)
    assert engine.completed_stimulus_count == 2
    assert engine.open_count == 1
    assert engine.start_count == 1
    assert backend.output_stream.start_count == 1
    engine.close()


def test_stimulus_can_cross_callback_boundary():
    backend = FakeBackend()
    engine = make_playback(backend)
    finish_warmup(backend, engine)
    start_sample = engine.frames_rendered + 120
    scheduled = engine.schedule_stimulus(
        np.ones((32, 1), dtype=np.float32) * 0.05,
        start_sample=start_sample,
    )

    first = push_output(backend, engine, 128)
    assert scheduled.state == "rendering"
    assert scheduled.position == 8
    second = push_output(backend, engine, 128)

    assert scheduled.state == "completed"
    np.testing.assert_allclose(first[120:], 0.05)
    np.testing.assert_allclose(second[:24], 0.05)
    assert np.count_nonzero(second[24:]) == 0
    engine.close()


def test_overlapping_stimuli_are_rejected():
    backend = FakeBackend()
    engine = make_playback(backend)
    finish_warmup(backend, engine)
    start_sample = engine.frames_rendered + 64
    engine.schedule_stimulus(np.ones((64, 1), dtype=np.float32), start_sample=start_sample)

    with pytest.raises(ValueError, match="重叠"):
        engine.schedule_stimulus(np.ones((8, 1), dtype=np.float32), start_sample=start_sample)
    engine.close()


def test_output_underflow_is_reported_and_stream_can_be_closed():
    backend = FakeBackend()
    engine = make_playback(backend)

    push_output(backend, engine, 128, AudioStreamStatus(output_underflow=True))

    assert engine.underflow_count == 1
    with pytest.raises(AudioStreamError, match="输出欠载"):
        engine.check_health()
    engine.close()
    engine.close()
    assert backend.output_stream.is_closed


def test_unexpected_output_stop_is_reported():
    backend = FakeBackend()
    engine = make_playback(backend)
    backend.output_stream.active = False

    with pytest.raises(AudioStreamError, match="意外停止"):
        engine.check_health()
    engine.close()


def test_capture_overflow_preserves_samples_and_reports_error():
    backend = FakeBackend()
    engine = make_capture(backend)
    samples = np.ones((32, 1), dtype=np.float32) * 0.25

    push_input(backend, samples, AudioStreamStatus(input_overflow=True))

    assert engine.overflow_count == 1
    with pytest.raises(AudioStreamError, match="输入溢出"):
        engine.check_health()
    np.testing.assert_array_equal(engine.read_all(), samples)
    assert engine.latest_timing.input_adc_time == pytest.approx(199.9)
    engine.close()


def test_capture_metadata_preserves_absolute_read_position():
    backend = FakeBackend()
    engine = make_capture(backend)
    push_input(backend, np.arange(32, dtype=np.float32)[:, np.newaxis])

    first = engine.read_with_metadata(16)
    second = engine.read_all_with_metadata()

    assert first.start_sample == 0
    assert first.end_sample == 16
    assert second.start_sample == 16
    assert second.end_sample == 32
    assert first.dropped_frames == second.dropped_frames == 0
    np.testing.assert_array_equal(first.samples.ravel(), np.arange(16))
    np.testing.assert_array_equal(second.samples.ravel(), np.arange(16, 32))
    engine.close()
