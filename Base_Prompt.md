# Project: Windows Headphone End-to-End Audio Latency Analyzer

## 1. Project Overview

Develop a professional Windows desktop application for measuring the end-to-end acoustic latency of headphones.

The application must be implemented using:

- Python 3.12+
- PySide6
- sounddevice / PortAudio
- NumPy
- SciPy
- SQLite
- pyqtgraph
- PyInstaller

The application should have a modern, professional, dark-themed UI suitable for an audio measurement and analysis tool.

The software must measure the complete acoustic audio path from the Windows playback system to an external wired microphone.

The application must not depend on headphone-specific drivers, proprietary SDKs, or internal wireless protocol information.

It must support:

- Wired headphones
- USB headphones
- 2.4 GHz wireless headphones
- Bluetooth headphones

The primary objective is to measure headphone latency during a continuous audio stream, rather than measuring the additional wake-up latency caused by starting an audio stream from an idle or low-power state.

---

## 2. Measurement Definition

The measurement path is:

Windows Audio Playback
→ Audio Output Device
→ Headphone Internal Processing
→ Headphone Driver
→ Acoustic Propagation
→ External Wired Microphone
→ Windows Audio Capture
→ Latency Analysis

The software must distinguish between:

1. Raw measured latency
2. Compensation value
3. Compensated latency
4. Measurement uncertainty / quality indicators

Use the following definition:

raw_latency_ms = capture_timestamp_ms - playback_timestamp_ms

compensated_latency_ms = raw_latency_ms - compensation_ms

The playback timestamp must represent the estimated actual playback time of the stimulus, not simply the Python function invocation time.

The capture timestamp must represent the estimated capture time of the detected acoustic response.

Do not treat time.perf_counter() at the beginning of a playback function as the actual acoustic playback timestamp.

Use audio stream timestamps, sample indices, and an appropriate timing-mapping mechanism.

If exact hardware playback timing cannot be established, clearly identify the limitations and avoid claiming that the result represents pure headphone-internal latency.

---

## 3. Audio Architecture

Create an independent audio subsystem.

Recommended modules:

audio/
- audio_device_manager.py
- playback_engine.py
- capture_engine.py
- audio_synchronizer.py
- stimulus_generator.py
- audio_backend.py

### 3.1 Output Device Selection

Provide a UI control for selecting the Windows playback device.

The application must:

- Enumerate available playback devices.
- Display device names.
- Display device identifiers.
- Display supported sample rates and channels when available.
- Allow refreshing the device list.
- Detect device disconnection.
- Handle invalid device selections gracefully.

### 3.2 Microphone Selection

Provide a separate UI control for selecting the microphone.

The microphone must be independent of the headphone output device.

The application must:

- Enumerate available recording devices.
- Display device names.
- Display device identifiers.
- Support mono and stereo capture.
- Allow selecting the input channel.
- Display live microphone input level.
- Detect microphone disconnection.
- Handle audio capture errors.

### 3.3 Audio Backend

Use sounddevice / PortAudio as the primary audio backend.

Prioritize Windows WASAPI Shared Mode.

Design the backend abstraction so that WASAPI Exclusive Mode can be added later.

Do not tightly couple the UI to sounddevice.

### 3.4 Continuous Playback Engine

The application must keep the playback stream open throughout the entire measurement session.

The playback stream must not be repeatedly opened and closed for individual tests.

Implement a continuous playback engine using a callback-based architecture.

The playback callback must:

- Maintain a continuous audio stream.
- Generate background audio.
- Insert measurement stimuli at scheduled sample positions.
- Maintain a monotonic sample counter.
- Avoid blocking operations.
- Avoid heavy numerical calculations.
- Avoid disk I/O.
- Avoid direct UI manipulation.
- Report playback timing information.
- Handle underflow conditions.

Use a thread-safe scheduling mechanism for test stimuli.

The measurement stimulus must be inserted into the existing audio stream without restarting the audio device.

---

## 4. Bluetooth Continuous-Stream Strategy

This requirement is critical.

The application must not measure the additional latency caused by starting a Bluetooth audio stream from an idle state.

Implement the following workflow:

1. Open the selected playback stream.
2. Start continuous background audio.
3. Keep the playback stream active.
4. Wait for a configurable warm-up period.
5. Optionally perform stability checks.
6. Begin formal measurements.
7. Insert test stimuli into the continuous stream.
8. Continue background playback between measurements.
9. Stop the stream only after the entire session has completed.

Default warm-up time: 3 seconds.

Allow the user to configure the warm-up duration.

Recommended configurable range: 1 to 15 seconds.

Background audio options:

- Low-level band-limited noise
- Low-level pink noise
- Continuous test sequence

The default background audio should have a low amplitude.

Do not assume that digital silence guarantees that a Bluetooth audio connection remains fully active.

The application must not claim to eliminate all Bluetooth power-saving behavior. It should instead implement a stable continuous-stream test procedure and report the test conditions.

The background signal must not interfere significantly with the detection of the measurement stimulus.

---

## 5. Stimulus Generator

Implement a configurable test signal generator.

The default stimulus should be a short band-limited Chirp signal.

Recommended default parameters:

- Sample rate: 48000 Hz
- Start frequency: 500 Hz
- End frequency: 8000 Hz
- Duration: 10 ms
- Window: Hann
- Peak amplitude: configurable
- Channel: mono by default

The stimulus generator must:

- Generate deterministic test signals.
- Support configurable frequency range.
- Support configurable duration.
- Support configurable amplitude.
- Avoid clipping.
- Support pre-generation of stimuli.
- Provide the reference waveform to the analysis subsystem.

The architecture should allow additional stimulus types later:

- Impulse
- MLS / PN sequence
- Custom WAV stimulus

Do not implement unnecessary stimulus types in the first MVP.

---

## 6. Latency Analyzer

Create:

core/latency_analyzer.py

Use NumPy and SciPy.

Implement:

- Sliding cross-correlation
- Correlation peak detection
- Sub-sample peak interpolation
- Signal amplitude estimation
- Noise floor estimation
- SNR estimation
- Signal quality evaluation
- Invalid measurement detection

Use a known reference stimulus to detect the corresponding microphone response.

A basic correlation model is:

R_xy[k] = sum(x[n] * y[n+k])

The detected correlation peak should be used to estimate the arrival time.

Do not use simple amplitude threshold detection as the only latency detection algorithm.

The algorithm must be robust against:

- Background noise
- Microphone gain variation
- Signal attenuation
- Different headphone frequency responses
- Correlation ambiguity
- Weak microphone signals

### Timing Synchronization

Playback and capture devices may use different hardware clocks.

The implementation must not assume that independently opened devices have perfectly synchronized clocks.

Use:

- Playback stream timestamps
- Capture stream timestamps
- Playback sample indices
- Capture sample indices
- Local correlation windows
- Timing offset estimation

Design the synchronization subsystem so that drift estimation and asynchronous sample-rate correction can be added later.

For the MVP, prioritize local correlation-based detection and a clear timing model.

Do not silently claim sample-accurate synchronization if the underlying backend cannot provide it.

### Measurement Quality

Each measurement must include:

- Raw latency
- Compensated latency
- Correlation peak
- Signal amplitude
- Estimated SNR
- Validity status
- Invalid reason
- Measurement timestamp

Invalid measurements must be preserved in the raw test data.

Do not automatically discard large latency values without recording the reason.

---

## 7. Compensation Manager

Create:

core/calibration_manager.py

Provide a configurable latency compensation value.

The compensation value must be displayed in milliseconds.

The user must be able to:

- Manually enter a compensation value.
- Set compensation to zero.
- Save calibration profiles.
- Select a calibration profile.
- View the calibration source.
- View calibration date.
- View calibration notes.

Calibration profiles should include:

- Playback device
- Capture device
- Sample rate
- Audio mode
- Compensation value
- Calibration method
- Calibration date
- Notes

Do not automatically invent or assume a compensation value.

If no calibration is available, use zero compensation and clearly display that the result is uncompensated.

---

## 8. Statistics Engine

Create:

core/statistics_engine.py

Support configurable test counts.

Default test count: 30.

Recommended options:

- 10
- 30
- 50
- 100

Calculate:

- Mean
- Median / P50
- P90
- P95
- Minimum
- Maximum
- Standard deviation
- Valid measurement count
- Invalid measurement count

Keep the original individual measurement results.

Do not calculate statistics using invalid measurements.

Do not remove valid outliers silently.

Provide a configurable minimum valid measurement count before a test session can be considered complete.

---

## 9. Test State Machine

Create:

core/test_state_machine.py

The test controller must use an explicit state machine.

Required states:

- IDLE
- DEVICE_CHECK
- OPENING_AUDIO
- WARMUP
- READY
- RUNNING
- ANALYZING
- WAIT_NEXT
- COMPLETED
- SAVED
- DEVICE_ERROR
- AUDIO_STREAM_ERROR
- CAPTURE_OVERFLOW
- PLAYBACK_UNDERFLOW
- NO_SIGNAL
- LOW_SNR
- USER_STOPPED

Each state must have clear entry and exit conditions.

The UI must display the current state.

The user must be able to stop a test safely.

Stopping a test must:

- Stop scheduling new stimuli.
- Finish or cancel pending processing safely.
- Stop continuous playback.
- Close the audio streams.
- Release audio resources.
- Preserve completed measurements.

The application must handle device disconnection and audio errors without crashing.

---

## 10. UI Architecture

Use PySide6 Qt Widgets.

Implement a professional dark-themed interface.

Recommended main window:

- Left navigation sidebar
- Main content area
- Top device selection section
- Central latency display
- Statistics section
- Waveform section
- Bottom control section
- Status bar

Main navigation:

1. Test
2. History
3. Comparison
4. Settings

### 10.1 Test Page

The test page must contain:

Device Selection:
- Output device
- Microphone device
- Refresh devices

Test Parameters:
- Test mode
- Test count
- Warm-up duration
- Compensation value
- Stimulus type
- Stimulus amplitude

Main Display:
- Current latency
- Average latency
- Median latency
- P95 latency
- Minimum latency
- Maximum latency
- Standard deviation
- Valid test count

Visualization:
- Output reference waveform
- Microphone response waveform
- Correlation peak
- Detection threshold
- Current latency marker

Controls:
- Start Test
- Stop Test
- Save Session
- Clear Current Session

Status Bar:
- Current audio state
- Output sample rate
- Capture sample rate
- Current test progress
- Audio error indicators

### 10.2 History Page

Provide a dedicated history interface.

The user must be able to:

- View all saved sessions.
- Search by headphone name.
- Filter by connection type.
- Sort by latency.
- Sort by test date.
- View detailed session information.
- Delete selected sessions.
- Edit notes.
- Export CSV.
- Export Excel.

### 10.3 Comparison Page

Allow selecting multiple saved test sessions.

Provide:

- Mean latency comparison chart
- Median latency comparison chart
- P95 comparison chart
- Latency distribution chart
- Per-test latency trend chart

Clearly distinguish between:

- Average latency
- Latency variation
- Measurement quality

Use pyqtgraph for interactive plots.

Avoid misleading chart scales.

### 10.4 Settings Page

Provide:

Audio Settings:
- Preferred sample rate
- Audio buffer configuration
- WASAPI Shared / Exclusive mode
- Input channel
- Output channel

Measurement Settings:
- Default test count
- Warm-up duration
- Background audio level
- Stimulus duration
- Stimulus frequency range
- Minimum SNR
- Compensation profiles

Application Settings:
- Theme
- Data storage location
- Log level
- Export defaults

---

## 11. Database

Use SQLite.

Create:

data/database.py
data/repository.py
data/models.py
data/exporter.py

Implement at least three entities:

### TestSession

Fields:

- id
- session_name
- headphone_name
- connection_type
- output_device
- input_device
- output_sample_rate
- input_sample_rate
- audio_mode
- compensation_ms
- test_count
- valid_count
- mean_latency_ms
- median_latency_ms
- p95_latency_ms
- min_latency_ms
- max_latency_ms
- std_latency_ms
- created_at
- notes

### TestResult

Fields:

- id
- session_id
- sequence
- raw_latency_ms
- compensated_latency_ms
- correlation_peak
- snr_db
- signal_level
- is_valid
- invalid_reason
- timestamp

### CalibrationProfile

Fields:

- id
- output_device
- input_device
- sample_rate
- audio_mode
- compensation_ms
- calibration_method
- calibration_date
- notes

Use parameterized SQL queries.

Implement database initialization and schema versioning.

Do not store only aggregate statistics.

---

## 12. Project Structure

Use the following project structure:

HeadphoneLatencyAnalyzer/
├── main.py
├── requirements.txt
├── README.md
├── app/
├── ui/
│   ├── main_window.py
│   ├── test_page.py
│   ├── history_page.py
│   ├── comparison_page.py
│   ├── settings_page.py
│   ├── widgets/
│   └── styles/
├── core/
│   ├── test_controller.py
│   ├── test_state_machine.py
│   ├── latency_analyzer.py
│   ├── calibration_manager.py
│   ├── statistics_engine.py
│   └── test_models.py
├── audio/
│   ├── audio_device_manager.py
│   ├── playback_engine.py
│   ├── capture_engine.py
│   ├── audio_synchronizer.py
│   ├── stimulus_generator.py
│   └── audio_backend.py
├── data/
│   ├── database.py
│   ├── repository.py
│   ├── models.py
│   └── exporter.py
├── tests/
└── logs/

Maintain strict separation between UI, audio, core logic, and persistence.

---

## 13. Threading and Performance

The UI must remain responsive during audio playback, capture, and analysis.

Use PySide6 QThread / QObject Worker patterns.

The audio callbacks must not:

- Update UI widgets directly.
- Perform database writes.
- Perform disk I/O.
- Perform expensive cross-correlation.
- Block waiting for other threads.
- Perform unnecessary memory allocations.

Use a thread-safe ring buffer for captured audio.

Use Qt signals to communicate measurement results to the UI.

Use a dedicated analysis worker for numerical processing.

Ensure proper cleanup when closing the application.

---

## 14. Testing and Validation

Implement unit tests for:

- Stimulus generation
- Cross-correlation
- Sub-sample interpolation
- Statistics
- Database operations
- Compensation calculations
- Test state transitions

Create synthetic test cases.

For example:

1. Generate a known reference signal.
2. Create a delayed copy.
3. Add noise.
4. Run the latency analyzer.
5. Verify the estimated delay.

The software must include an offline simulation mode.

The simulation mode must allow testing the UI and analysis pipeline without physical audio devices.

The simulation must not be presented as a real headphone latency measurement.

### Acceptance Criteria

The MVP is complete when:

1. The application launches on Windows.
2. Playback and capture devices can be selected independently.
3. A continuous playback stream can be maintained.
4. A measurement stimulus can be inserted without restarting the stream.
5. The microphone can capture the corresponding response.
6. The latency analyzer can estimate the response delay.
7. Raw and compensated latency are displayed correctly.
8. Multiple measurements can be performed.
9. Statistics are calculated correctly.
10. Test sessions can be saved to SQLite.
11. Saved sessions can be browsed and compared.
12. Data can be exported to CSV.
13. Audio errors do not crash the application.
14. The UI remains responsive during testing.
15. Bluetooth tests use continuous-stream warm-up rather than repeated stream restarts.

---

## 15. Development Instructions

Do not generate the entire application in one step.

Develop the application incrementally.

### Phase 1: Project Foundation

- Create project structure.
- Create PySide6 main window.
- Implement dark theme.
- Implement navigation.
- Implement device enumeration.
- Implement configuration management.

### Phase 2: Audio Engine

- Implement playback engine.
- Implement microphone capture engine.
- Implement continuous background playback.
- Implement stimulus scheduling.
- Implement audio stream lifecycle management.

### Phase 3: Latency Analysis

- Implement Chirp generation.
- Implement sliding cross-correlation.
- Implement timestamp mapping.
- Implement compensation.
- Implement measurement quality indicators.
- Implement offline simulation.

### Phase 4: UI Integration

- Implement the test page.
- Implement live latency display.
- Implement waveform visualization.
- Implement progress indicators.
- Implement safe start / stop operations.

### Phase 5: Data Persistence

- Implement SQLite.
- Implement test sessions.
- Implement individual test results.
- Implement history page.
- Implement CSV export.

### Phase 6: Comparison and Polish

- Implement comparison charts.
- Implement latency distribution charts.
- Implement calibration profiles.
- Improve error handling.
- Improve UI responsiveness.
- Add Windows packaging.

For every phase:

1. Explain the implementation plan.
2. Create or modify the required files.
3. Ensure the code is runnable.
4. Add relevant tests.
5. Explain how to execute the application.
6. Report known limitations.

Prioritize a reliable measurement pipeline over decorative UI features.

Do not use placeholder implementations for the core latency algorithm.

Do not fabricate audio timestamps or calibration values.

Do not silently discard measurements.

The application must clearly distinguish actual measurement results from simulation results and estimated timing values.