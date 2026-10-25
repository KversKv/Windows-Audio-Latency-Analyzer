import pytest

from core.test_state_machine import InvalidStateTransition
from core.test_state_machine import TestState as SessionState
from core.test_state_machine import TestStateMachine as StateMachine


def _advance(machine: StateMachine, states: list[SessionState]) -> None:
    for state in states:
        machine.transition(state)


def test_complete_measurement_and_save_path():
    machine = StateMachine()
    path = [
        SessionState.DEVICE_CHECK,
        SessionState.OPENING_AUDIO,
        SessionState.WARMUP,
        SessionState.READY,
        SessionState.RUNNING,
        SessionState.ANALYZING,
        SessionState.WAIT_NEXT,
        SessionState.RUNNING,
        SessionState.ANALYZING,
        SessionState.COMPLETED,
        SessionState.SAVED,
    ]

    _advance(machine, path)

    assert machine.state == SessionState.SAVED
    assert machine.is_active is False
    assert machine.is_error is False


def test_quality_states_do_not_discard_session_flow():
    machine = StateMachine()
    _advance(
        machine,
        [
            SessionState.DEVICE_CHECK,
            SessionState.READY,
            SessionState.RUNNING,
            SessionState.ANALYZING,
            SessionState.NO_SIGNAL,
            SessionState.WAIT_NEXT,
            SessionState.RUNNING,
            SessionState.ANALYZING,
            SessionState.LOW_SNR,
            SessionState.COMPLETED,
        ],
    )

    assert machine.state == SessionState.COMPLETED


def test_invalid_transition_is_rejected_without_changing_state():
    machine = StateMachine()

    with pytest.raises(InvalidStateTransition, match="IDLE.*RUNNING"):
        machine.transition(SessionState.RUNNING)

    assert machine.state == SessionState.IDLE


@pytest.mark.parametrize(
    "error_state",
    [
        SessionState.DEVICE_ERROR,
        SessionState.AUDIO_STREAM_ERROR,
        SessionState.CAPTURE_OVERFLOW,
        SessionState.PLAYBACK_UNDERFLOW,
        SessionState.USER_STOPPED,
    ],
)
def test_stream_failure_and_stop_preserve_recovery_path(error_state):
    machine = StateMachine()
    _advance(
        machine,
        [SessionState.DEVICE_CHECK, SessionState.OPENING_AUDIO, SessionState.WARMUP],
    )

    machine.transition(error_state)

    assert machine.is_active is False
    assert machine.is_error is (error_state != SessionState.USER_STOPPED)
    machine.transition(SessionState.IDLE)
    assert machine.state == SessionState.IDLE


def test_simulation_can_skip_stream_opening_and_warmup():
    machine = StateMachine()

    _advance(
        machine,
        [SessionState.DEVICE_CHECK, SessionState.READY, SessionState.RUNNING, SessionState.ANALYZING],
    )

    assert machine.state == SessionState.ANALYZING
    assert machine.is_active is True
