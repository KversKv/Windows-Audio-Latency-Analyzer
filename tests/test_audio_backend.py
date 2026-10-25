from types import SimpleNamespace

import numpy as np

from audio import audio_backend as backend_module
from audio.audio_backend import SimulatedAudioBackend, SoundDeviceBackend
from audio.audio_device_manager import AudioDeviceManager


def test_simulated_backend_has_independent_devices():
    manager = AudioDeviceManager(SimulatedAudioBackend(sample_rate=44100))

    inventory = manager.scan()

    assert inventory.is_simulation is True
    assert len(inventory.output_devices) == 1
    assert len(inventory.input_devices) == 1
    assert inventory.output_devices[0].identifier != inventory.input_devices[0].identifier
    assert inventory.output_devices[0].default_sample_rate == 44100


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
    monkeypatch.setattr(backend_module.os, "name", "nt")

    inventory = AudioDeviceManager(SoundDeviceBackend()).scan()

    assert len(inventory.output_devices) == 1
    assert len(inventory.input_devices) == 1
    assert inventory.output_devices[0].name == "WASAPI 耳机"
    assert inventory.input_devices[0].identifier == 1


def test_sounddevice_stream_maps_callback_timing_and_status(monkeypatch):
    created_streams = []

    class FakeRawOutputStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.callback = kwargs["callback"]
            self.active = False
            self.closed = False
            created_streams.append(self)

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(backend_module.sd, "OutputStream", FakeRawOutputStream)
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
    stream.stop()
    stream.close()
    assert raw_stream.closed
