from types import SimpleNamespace

import numpy as np
import pytest

from audio import pyaudiowpatch_backend as backend_module
from audio.audio_backend import AudioBackendError, AudioDevice


def _endpoint(index, name, *, output=False, loopback=False, channels=2):
    return {
        "index": index,
        "name": name,
        "maxInputChannels": 0 if output else channels,
        "maxOutputChannels": channels if output else 0,
        "defaultSampleRate": 48000.0,
        "isLoopbackDevice": loopback,
    }


class _FakeRawStream:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["stream_callback"]
        self.active = False
        self.closed = False

    def start_stream(self):
        self.active = True

    def stop_stream(self):
        self.active = False

    def is_active(self):
        return self.active and not self.closed

    def close(self):
        self.active = False
        self.closed = True

    def get_input_latency(self):
        return 0.02

    def get_output_latency(self):
        return 0.01


class _FakePyAudio:
    def __init__(self, *, outputs=None, loopbacks=None):
        self.outputs = outputs if outputs is not None else [_endpoint(10, "测试耳机", output=True)]
        self.loopbacks = (
            loopbacks
            if loopbacks is not None
            else [_endpoint(11, "测试耳机 [Loopback]", loopback=True)]
        )
        self.microphone = _endpoint(12, "普通麦克风")
        self.open_calls = []
        self.streams = []
        self.terminated = False

    def open(self, **kwargs):
        self.open_calls.append(kwargs)
        stream = _FakeRawStream(kwargs)
        self.streams.append(stream)
        return stream

    def get_device_info_generator_by_host_api(self, *, host_api_type):
        yield from self.outputs
        yield self.microphone
        yield from self.loopbacks

    def get_loopback_device_info_generator(self):
        yield from self.loopbacks

    def get_host_api_info_by_type(self, host_api_type):
        return {"name": "Windows WASAPI"}

    def terminate(self):
        self.terminated = True


def _api(audio):
    return SimpleNamespace(
        PyAudio=lambda: audio,
        paWASAPI=13,
        paFloat32=1,
        paContinue=0,
        paAbort=2,
        paInputUnderflow=1,
        paInputOverflow=2,
        paOutputUnderflow=4,
        paOutputOverflow=8,
        paPrimingOutput=16,
        get_portaudio_version_text=lambda: "PortAudio V19.7.0 (synthetic shared QPC clock)",
    )


def _make_backend(monkeypatch, audio):
    monkeypatch.setattr(backend_module, "_load_pyaudiowpatch", lambda: _api(audio))
    device = AudioDevice(3, "测试耳机", "Windows WASAPI", 0, 2, 48000.0)
    return backend_module.PyAudioWPatchBackend(device)


def _time(adc=0.0, dac=0.0):
    return {
        "input_buffer_adc_time": adc,
        "current_time": 50.0,
        "output_buffer_dac_time": dac,
    }


def test_loopback_streams_share_instance_and_map_real_callback_metadata(monkeypatch):
    audio = _FakePyAudio()
    backend = _make_backend(monkeypatch, audio)
    output_received = []
    input_received = []

    def output_callback(samples, frames, time_info, status):
        samples[:] = np.arange(frames * 2, dtype=np.float32).reshape(frames, 2) / 10
        output_received.append((time_info.output_buffer_dac_time, time_info.clock_domain, status))

    def input_callback(samples, frames, time_info, status):
        input_received.append((samples.copy(), time_info.input_buffer_adc_time, time_info.clock_domain, status))

    output = backend.open_output_stream(
        device_id=3,
        sample_rate=48000,
        channels=2,
        blocksize=2,
        callback=output_callback,
    )
    capture = backend.open_input_stream(
        device_id=None,
        sample_rate=48000,
        channels=1,
        blocksize=2,
        callback=input_callback,
    )

    assert len(audio.open_calls) == 2
    assert audio.open_calls[0]["output_device_index"] == 10
    assert audio.open_calls[1]["input_device_index"] == 11
    assert "input" not in audio.open_calls[0] or not audio.open_calls[0]["input"]
    assert not audio.open_calls[1].get("output", False)

    output_data, output_flag = audio.streams[0].callback(None, 2, _time(dac=50.02), 4)
    loopback_samples = np.array([[0.1, 0.9], [0.2, 0.8]], dtype=np.float32)
    input_data, input_flag = audio.streams[1].callback(
        loopback_samples.tobytes(),
        2,
        _time(adc=50.01),
        2,
    )

    assert output_flag == input_flag == 0
    assert input_data is None
    assert len(output_data) == 2 * 2 * 4
    assert output_received[0][0] == 50.02
    assert output_received[0][2].output_underflow is True
    np.testing.assert_array_equal(input_received[0][0], loopback_samples[:, :1])
    assert input_received[0][1] == 50.01
    assert input_received[0][3].input_overflow is True
    assert output_received[0][1] == input_received[0][2] == backend.clock_domain
    output_info = output.stream_info
    capture_info = capture.stream_info
    assert output_info.device_id == 10
    assert output_info.output_latency_seconds == 0.01
    assert output_info.input_latency_seconds is None
    assert output_info.endpoint_default_sample_rate == 48000.0
    assert capture_info.device_id == 11
    assert capture_info.input_latency_seconds == 0.02
    assert capture_info.native_channels == 2

    output.start()
    capture.start()
    assert output.is_active and capture.is_active
    backend.close()
    assert output.is_closed and capture.is_closed
    assert audio.terminated is True


def test_loopback_channel_expansion_is_deterministic(monkeypatch):
    audio = _FakePyAudio(loopbacks=[_endpoint(11, "测试耳机 [Loopback]", loopback=True, channels=1)])
    backend = _make_backend(monkeypatch, audio)
    received = []
    backend.open_output_stream(
        device_id=3,
        sample_rate=48000,
        channels=2,
        blocksize=2,
        callback=lambda samples, frames, time_info, status: samples.fill(0),
    )
    backend.open_input_stream(
        device_id=None,
        sample_rate=48000,
        channels=2,
        blocksize=2,
        callback=lambda samples, frames, time_info, status: received.append(samples.copy()),
    )

    samples = np.array([[0.25], [0.5]], dtype=np.float32)
    audio.streams[1].callback(samples.tobytes(), 2, _time(adc=50.0), 0)

    np.testing.assert_array_equal(received[0], np.repeat(samples, 2, axis=1))
    backend.close()


def test_duplicate_output_endpoints_are_rejected_without_opening_stream(monkeypatch):
    audio = _FakePyAudio(
        outputs=[
            _endpoint(10, "测试耳机", output=True),
            _endpoint(13, "测试耳机", output=True),
        ]
    )
    backend = _make_backend(monkeypatch, audio)

    with pytest.raises(AudioBackendError, match="唯一匹配"):
        backend.open_output_stream(
            device_id=3,
            sample_rate=48000,
            channels=2,
            blocksize=0,
            callback=lambda samples, frames, time_info, status: None,
        )
    assert audio.open_calls == []
    backend.close()


def test_missing_loopback_never_falls_back_to_microphone(monkeypatch):
    audio = _FakePyAudio(loopbacks=[])
    backend = _make_backend(monkeypatch, audio)
    backend.open_output_stream(
        device_id=3,
        sample_rate=48000,
        channels=2,
        blocksize=0,
        callback=lambda samples, frames, time_info, status: samples.fill(0),
    )

    with pytest.raises(AudioBackendError, match=r"\[Loopback\]"):
        backend.open_input_stream(
            device_id=None,
            sample_rate=48000,
            channels=1,
            blocksize=0,
            callback=lambda samples, frames, time_info, status: None,
        )
    assert len(audio.open_calls) == 1
    assert all(call.get("input_device_index") != 12 for call in audio.open_calls)
    backend.close()


def test_ordinary_input_device_is_rejected(monkeypatch):
    audio = _FakePyAudio()
    backend = _make_backend(monkeypatch, audio)
    backend.open_output_stream(
        device_id=3,
        sample_rate=48000,
        channels=2,
        blocksize=0,
        callback=lambda samples, frames, time_info, status: samples.fill(0),
    )

    with pytest.raises(AudioBackendError, match="禁止选择麦克风"):
        backend.open_input_stream(
            device_id=12,
            sample_rate=48000,
            channels=1,
            blocksize=0,
            callback=lambda samples, frames, time_info, status: None,
        )
    assert len(audio.open_calls) == 1
    backend.close()


def test_capability_requires_real_loopback_endpoint(monkeypatch):
    audio = _FakePyAudio(loopbacks=[])
    monkeypatch.setattr(backend_module.os, "name", "nt")
    monkeypatch.setattr(backend_module, "_load_pyaudiowpatch", lambda: _api(audio))

    unavailable = backend_module.detect_software_loopback_capability()
    assert unavailable.supported is False
    assert "没有可用" in unavailable.detail

    audio.loopbacks = [_endpoint(11, "测试耳机 [Loopback]", loopback=True)]
    available = backend_module.detect_software_loopback_capability()
    assert available.supported is True
    assert "1 个" in available.detail


def test_capability_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(backend_module.os, "name", "nt")

    def missing_dependency():
        raise ImportError("测试缺失")

    monkeypatch.setattr(backend_module, "_load_pyaudiowpatch", missing_dependency)
    capability = backend_module.detect_software_loopback_capability()

    assert capability.supported is False
    assert "PyAudioWPatch 不可用" in capability.detail
