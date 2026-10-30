from enum import Enum, auto


class TestState(Enum):
    IDLE = auto()
    DEVICE_CHECK = auto()
    OPENING_AUDIO = auto()
    WARMUP = auto()
    READY = auto()
    RUNNING = auto()
    ANALYZING = auto()
    WAIT_NEXT = auto()
    COMPLETED = auto()
    SAVED = auto()
    DEVICE_ERROR = auto()
    AUDIO_STREAM_ERROR = auto()
    CAPTURE_OVERFLOW = auto()
    PLAYBACK_UNDERFLOW = auto()
    NO_SIGNAL = auto()
    LOW_SNR = auto()
    USER_STOPPED = auto()


class InvalidStateTransition(ValueError):
    pass


_ACTIVE_STATES = frozenset(
    {
        TestState.DEVICE_CHECK,
        TestState.OPENING_AUDIO,
        TestState.WARMUP,
        TestState.READY,
        TestState.RUNNING,
        TestState.ANALYZING,
        TestState.WAIT_NEXT,
        TestState.NO_SIGNAL,
        TestState.LOW_SNR,
    }
)

_ERROR_STATES = frozenset(
    {
        TestState.DEVICE_ERROR,
        TestState.AUDIO_STREAM_ERROR,
        TestState.CAPTURE_OVERFLOW,
        TestState.PLAYBACK_UNDERFLOW,
    }
)

_RECOVERY_STATES = frozenset({TestState.IDLE, TestState.DEVICE_CHECK})
_STREAM_FAILURE_STATES = frozenset(
    {
        TestState.DEVICE_ERROR,
        TestState.AUDIO_STREAM_ERROR,
        TestState.CAPTURE_OVERFLOW,
        TestState.PLAYBACK_UNDERFLOW,
        TestState.USER_STOPPED,
    }
)

_TRANSITIONS = {
    TestState.IDLE: frozenset({TestState.DEVICE_CHECK}),
    TestState.DEVICE_CHECK: frozenset(
        {TestState.OPENING_AUDIO, TestState.READY, TestState.DEVICE_ERROR, TestState.USER_STOPPED}
    ),
    TestState.OPENING_AUDIO: frozenset(
        {TestState.WARMUP, TestState.DEVICE_ERROR, TestState.AUDIO_STREAM_ERROR, TestState.USER_STOPPED}
    ),
    TestState.WARMUP: frozenset({TestState.READY, *_STREAM_FAILURE_STATES}),
    TestState.READY: frozenset({TestState.RUNNING, *_STREAM_FAILURE_STATES}),
    TestState.RUNNING: frozenset({TestState.ANALYZING, *_STREAM_FAILURE_STATES}),
    TestState.ANALYZING: frozenset(
        {
            TestState.WAIT_NEXT,
            TestState.COMPLETED,
            TestState.NO_SIGNAL,
            TestState.LOW_SNR,
            *_STREAM_FAILURE_STATES,
        }
    ),
    TestState.WAIT_NEXT: frozenset({TestState.RUNNING, TestState.COMPLETED, *_STREAM_FAILURE_STATES}),
    TestState.NO_SIGNAL: frozenset(
        {TestState.WAIT_NEXT, TestState.COMPLETED, *_STREAM_FAILURE_STATES}
    ),
    TestState.LOW_SNR: frozenset(
        {TestState.WAIT_NEXT, TestState.COMPLETED, *_STREAM_FAILURE_STATES}
    ),
    TestState.COMPLETED: frozenset({TestState.SAVED, *_RECOVERY_STATES}),
    TestState.SAVED: _RECOVERY_STATES,
    TestState.USER_STOPPED: frozenset({TestState.SAVED, *_RECOVERY_STATES}),
    TestState.DEVICE_ERROR: frozenset({TestState.SAVED, *_RECOVERY_STATES}),
    TestState.AUDIO_STREAM_ERROR: frozenset({TestState.SAVED, *_RECOVERY_STATES}),
    TestState.CAPTURE_OVERFLOW: frozenset({TestState.SAVED, *_RECOVERY_STATES}),
    TestState.PLAYBACK_UNDERFLOW: frozenset({TestState.SAVED, *_RECOVERY_STATES}),
}


class TestStateMachine:
    def __init__(self) -> None:
        self._state = TestState.IDLE

    @property
    def state(self) -> TestState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state in _ACTIVE_STATES

    @property
    def is_error(self) -> bool:
        return self._state in _ERROR_STATES

    def can_transition(self, target: TestState) -> bool:
        return target in _TRANSITIONS[self._state]

    def transition(self, target: TestState) -> TestState:
        if not self.can_transition(target):
            raise InvalidStateTransition(f"不允许从 {self._state.name} 转换到 {target.name}")
        self._state = target
        return self._state
