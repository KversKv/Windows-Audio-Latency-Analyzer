from types import SimpleNamespace

import numpy as np
import pytest

from audio import audio_backend as backend_module
from audio.audio_backend import (
    AudioBackendError,
    SimulatedAudioBackend,
    SoftwareLoopbackCapability,
    SoundDeviceBackend,
)
from audio import pyaudiowpatch_backend as loopback_module
from audio.audio_device_manager import AudioDeviceManager


def test_simulated_backend_has_independent_devices():
    manager = AudioDeviceManager(SimulatedAudioBackend(sample_rate=44100))

    inventory = manager.scan()

    assert inventory.is_simulation is True
    assert len(inventory.output_devices) == 1
    assert len(inventory.input_devices) == 1
    assert inventory.output_devices[0].identifier != inventory.input_devices[0].identifier
    assert inventory.output_devices[0].default_sample_rate == 44100
    assert inventory.software_loopback_capability.supported is False
    assert "离线模拟" in inventory.software_loopback_capability.detail


def test_sounddevice_backend_prefers_wasapi_on_windows(monkeypatch):
    host_apis = [{"name": "Windows WASAPI"}, {"name": "MME"}]
    raw_devices = [
        {
            "name": "WASAPI 耳机",
            "hostapi": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": "WASAPI 麦克风",
            "hostapi": 0,
            "max_input_channels": 2,
            "max_output_channels": 0,
            "default_samplerate": 44100.0,
        },
        {
            "name": "MME 重复设备",
            "hostapi": 1,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
    ]
    monkeypatch.setattr(backend_module.sd, "query_devices", lambda: raw_devices)
    monkeypatch.setattr(backend_module.sd, "query_hostapis", lambda: host_apis)
    monkeypatch.setattr(
        backend_module.sd,
        "_lib",
        SimpleNamespace(PaWasapi_UpdateDeviceList=lambda: 0),
    )
    monkeypatch.setattr(backend_module.os, "name", "nt")

    inventory = AudioDeviceManager(SoundDeviceBackend()).scan()

    assert len(inventory.output_devices) == 1
    assert len(inventory.input_devices) == 1
    assert inventory.output_devices[0].name == "WASAPI 耳机"
    assert inventory.input_devices[0].identifier == 1


def test_sounddevice_backend_updates_wasapi_before_listing(monkeypatch):
    calls = []
    raw_device = {
        "name": "新插入耳机",
        "hostapi": 0,
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
    }

    def query_hostapis():
        calls.append("hostapis")
        return [{"name": "Windows WASAPI"}]

    def update_device_list():
        calls.append("update")
        return 0

    def query_devices():
        calls.append("devices")
        return [raw_device]

    monkeypatch.setattr(backend_module.sd, "query_hostapis", query_hostapis)
    monkeypatch.setattr(backend_module.sd, "query_devices", query_devices)
    monkeypatch.setattr(
        backend_module.sd,
        "_lib",
        SimpleNamespace(PaWasapi_UpdateDeviceList=update_device_list),
    )
    monkeypatch.setattr(backend_module.os, "name", "nt")

    inventory = AudioDeviceManager(SoundDeviceBackend()).scan()

    assert calls == ["hostapis", "update", "devices"]
    assert [device.name for device in inventory.output_devices] == ["新插入耳机"]


def test_sounddevice_backend_does_not_return_stale_devices_after_update_failure(monkeypatch):
    device_queries = []
    monkeypatch.setattr(
        backend_module.sd,
        "query_hostapis",
        lambda: [{"name": "Windows WASAPI"}],
    )
    monkeypatch.setattr(
        backend_module.sd,
        "query_devices",
        lambda: device_queries.append(True) or [],
    )
    monkeypatch.setattr(
        backend_module.sd,
        "_lib",
        SimpleNamespace(PaWasapi_UpdateDeviceList=lambda: -9999),
    )

    def fail_check(_error, _message=""):
        raise backend_module.sd.PortAudioError("原生设备表更新失败")

    monkeypatch.setattr(backend_module.sd, "_check", fail_check)
    monkeypatch.setattr(backend_module.os, "name", "nt")

    with pytest.raises(AudioBackendError, match="原生设备表更新失败"):
        SoundDeviceBackend().list_devices()
    assert device_queries == []


@pytest.mark.parametrize("initialized", [0, 1])
def test_sounddevice_backend_reinitializes_portaudio_when_required(
    monkeypatch,
    initialized,
):
    calls = []
    raw_device = {
        "name": "新插入耳机",
        "hostapi": 0,
        "max_input_channels": 0,
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
    }
    monkeypatch.setattr(backend_module.os, "name", "nt")
    monkeypatch.setattr(backend_module.sd, "_initialized", initialized)
    monkeypatch.setattr(
        backend_module.sd,
        "query_hostapis",
        lambda: [{"name": "Windows WASAPI"}],
    )
    monkeypatch.setattr(
        backend_module.sd,
        "_lib",
        SimpleNamespace(
            PaWasapi_UpdateDeviceList=lambda: calls.append("update") or -9986
        ),
    )

    def terminate():
        calls.append("terminate")
        backend_module.sd._initialized = 0

    def initialize():
        calls.append("initialize")
        backend_module.sd._initialized = 1

    def query_devices():
        calls.append("devices")
        assert backend_module.sd._initialized == 1
        return [raw_device]

    monkeypatch.setattr(backend_module.sd, "_terminate", terminate)
    monkeypatch.setattr(backend_module.sd, "_initialize", initialize)
    monkeypatch.setattr(backend_module.sd, "query_devices", query_devices)

    inventory = AudioDeviceManager(SoundDeviceBackend()).scan()

    expected_calls = (
        ["update", "terminate", "initialize", "devices"]
        if initialized
        else ["initialize", "devices"]
    )
    assert calls == expected_calls
    assert [device.name for device in inventory.output_devices] == ["新插入耳机"]


def test_sounddevice_backend_skips_wasapi_update_off_windows(monkeypatch):
    def unexpected_update():
        raise AssertionError("非 Windows 不应更新 WASAPI 设备表")

    monkeypatch.setattr(
        backend_module.sd,
        "query_hostapis",
        lambda: [{"name": "Windows WASAPI"}],
    )
    monkeypatch.setattr(backend_module.sd, "query_devices", lambda: [])
    monkeypatch.setattr(
        backend_module.sd,
        "_lib",
        SimpleNamespace(PaWasapi_UpdateDeviceList=unexpected_update),
    )
    monkeypatch.setattr(backend_module.os, "name", "posix")

    assert SoundDeviceBackend().list_devices() == []


def test_sounddevice_stream_maps_callback_timing_and_status(monkeypatch):
    created_streams = []

    class FakeRawOutputStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.callback = kwargs["callback"]
            self.active = False
            self.closed = False
            self.device = kwargs["device"]
            self.channels = kwargs["channels"]
            self.samplerate = kwargs["samplerate"]
            self.blocksize = kwargs["blocksize"]
            self.latency = 0.01
            created_streams.append(self)

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(backend_module.sd, "OutputStream", FakeRawOutputStream)
    monkeypatch.setattr(
        backend_module.sd,
        "query_devices",
        lambda device_id: {
            "name": "测试输出",
            "hostapi": 1,
            "default_samplerate": 48000.0,
        },
    )
    monkeypatch.setattr(
        backend_module.sd,
        "query_hostapis",
        lambda host_api: {"name": "Windows WASAPI"},
    )
    received = []

    def callback(outdata, frames, time_info, status):
        received.append((frames, time_info.output_buffer_dac_time, status.output_underflow))
        outdata.fill(0)

    stream = SoundDeviceBackend().open_output_stream(
        device_id=3,
        sample_rate=48000,
        channels=1,
        blocksize=128,
        callback=callback,
    )
    raw_stream = created_streams[0]
    outdata = np.empty((128, 1), dtype=np.float32)
    raw_time = SimpleNamespace(
        inputBufferAdcTime=0.0,
        currentTime=10.0,
        outputBufferDacTime=10.02,
    )
    raw_status = SimpleNamespace(
        input_underflow=False,
        input_overflow=False,
        output_underflow=True,
        output_overflow=False,
        priming_output=False,
    )

    stream.start()
    raw_stream.callback(outdata, 128, raw_time, raw_status)

    assert received == [(128, 10.02, True)]
    assert raw_stream.kwargs["device"] == 3
    assert raw_stream.kwargs["samplerate"] == 48000
    assert np.count_nonzero(outdata) == 0
    info = stream.stream_info
    assert info.device_id == 3
    assert info.device_name == "测试输出"
    assert info.host_api == "Windows WASAPI"
    assert info.sample_rate == 48000.0
    assert info.blocksize == 128
    assert info.latency_seconds == 0.01
    stream.stop()
    stream.close()
    assert raw_stream.closed


def test_software_loopback_is_unavailable_off_windows(monkeypatch):
    monkeypatch.setattr(backend_module.os, "name", "posix")

    capability = SoundDeviceBackend().software_loopback_capability()

    assert capability.supported is False
    assert "仅支持 Windows WASAPI" in capability.detail


def test_sounddevice_backend_delegates_loopback_capability_to_wpatch(monkeypatch):
    expected = SoftwareLoopbackCapability(True, "测试数字环回可用")
    monkeypatch.setattr(
        loopback_module,
        "detect_software_loopback_capability",
        lambda: expected,
    )

    assert SoundDeviceBackend().software_loopback_capability() is expected


def test_device_scan_degrades_when_loopback_capability_check_fails():
    class FailingCapabilityBackend(SimulatedAudioBackend):
        def software_loopback_capability(self):
            raise RuntimeError("测试能力探测失败")

    inventory = AudioDeviceManager(FailingCapabilityBackend(sample_rate=48000)).scan()

    assert len(inventory.output_devices) == 1
    assert len(inventory.input_devices) == 1
    assert inventory.software_loopback_capability.supported is False
    assert "测试能力探测失败" in inventory.software_loopback_capability.detail
