import os
import sqlite3
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from core.persistence_controller import PersistenceController
from core.persistence_service import PersistenceService
from tests.test_persistence import _snapshot
from ui.comparison_page import ComparisonPage


@pytest.fixture
def qt_application():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def comparison_context(qt_application, tmp_path):
    service = PersistenceService(tmp_path / "comparison-ui.db")
    controller = PersistenceController(service)
    page = ComparisonPage(controller)
    page.show()
    yield service, controller, page
    controller.shutdown()
    page.close()
    page.deleteLater()
    controller.deleteLater()
    qt_application.processEvents()


def _wait_for_signal(signal, timeout_ms=3000):
    event_loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(event_loop.quit)
    signal.connect(event_loop.quit)
    timer.start(timeout_ms)
    event_loop.exec()
    signal.disconnect(event_loop.quit)
    received = timer.isActive()
    timer.stop()
    return received


def _save_two_sessions(service):
    first_id = service.save_session(
        _snapshot("comparison-first", "Alpha", "bluetooth", with_calibration=True)
    )
    second_id = service.save_session(
        _snapshot("comparison-second", "Beta", "wired", latency_offset_ms=10.0)
    )
    return first_id, second_id


def _refresh_sessions(qt_application, controller, page):
    page.refresh()
    assert _wait_for_signal(controller.comparison_session_list_loaded) is True
    qt_application.processEvents()


def _compare_all(qt_application, controller, page):
    page.session_table.selectAll()
    assert len(page.selected_session_ids()) == 2
    assert page.compare_button.isEnabled() is True
    page.compare_button.click()
    assert _wait_for_signal(controller.comparison_sessions_loaded) is True
    qt_application.processEvents()


def _condition_row(page, label):
    return next(
        row
        for row in range(page.conditions_table.rowCount())
        if page.conditions_table.item(row, 0).text() == label
    )


def _all_invalid_snapshot(session_id, headphone_name):
    snapshot = _snapshot(session_id, headphone_name)
    records = tuple(
        replace(
            record,
            measurement=replace(
                record.measurement,
                is_valid=False,
                invalid_reason="synthetic_invalid_for_empty_comparison",
            ),
        )
        for record in snapshot.records
    )
    return replace(snapshot, records=records)


def test_comparison_service_preserves_order_and_recalculates_from_results(tmp_path):
    service = PersistenceService(tmp_path / "comparison.db")
    first_id, second_id = _save_two_sessions(service)
    with sqlite3.connect(service.database.path) as connection:
        connection.execute(
            "UPDATE test_sessions SET mean_latency_ms = ? WHERE id = ?",
            (999.0, first_id),
        )

    comparisons = service.load_comparison_sessions([second_id, first_id])

    assert [comparison.session.id for comparison in comparisons] == [second_id, first_id]
    assert [comparison.statistics.raw.mean_latency_ms for comparison in comparisons] == [
        pytest.approx(35.0),
        pytest.approx(25.0),
    ]
    assert comparisons[1].session.mean_latency_ms == 999.0
    assert comparisons[1].statistics.raw.mean_latency_ms == pytest.approx(25.0)
    assert comparisons[1].statistics.compensated.mean_latency_ms == pytest.approx(23.0)
    for comparison in comparisons:
        stored = service.get_session(comparison.session.id)
        expected = service.recalculate_statistics(stored)
        assert comparison.statistics == expected
        assert comparison.statistics.valid_count == 2
        assert comparison.statistics.invalid_count == 1
        assert len(comparison.session.results) == 3
        assert comparison.session.results[-1].is_valid is False
        assert comparison.session.results[-1].invalid_reason == "latency_out_of_range"


def test_comparison_page_charts_match_recalculated_statistics_and_raw_samples(
    comparison_context, qt_application
):
    service, controller, page = comparison_context
    _save_two_sessions(service)
    _refresh_sessions(qt_application, controller, page)
    _compare_all(qt_application, controller, page)

    assert page.analysis_scroll.isVisible() is True
    assert page.analysis_empty_label.isVisible() is False
    assert len(page._comparisons) == 2
    assert set(page.statistics_bars) == {"mean", "median", "p95"}
    assert page.statistics_table.rowCount() == 2
    assert page.statistics_plot.getViewBox().viewRange()[1][0] == pytest.approx(0.0)
    assert "不代表测量精度" in page.precision_label.text()
    assert page.simulation_banner.isVisible() is True

    for row, comparison in enumerate(page._comparisons):
        expected = service.recalculate_statistics(comparison.session)
        assert comparison.statistics == expected
        assert page.statistics_table.item(row, 2).text() == "2 / 3"
        assert page.statistics_table.item(row, 3).text() == "1"
        for column, value in (
            (4, expected.raw.mean_latency_ms),
            (5, expected.raw.median_latency_ms),
            (6, expected.raw.p95_latency_ms),
        ):
            assert page.statistics_table.item(row, column).text() == f"{value:.2f} ms"
        valid_results = [result for result in comparison.session.results if result.is_valid]
        curve = page.trend_curves[comparison.session.id]
        np.testing.assert_array_equal(curve.xData, [1.0, 2.0])
        np.testing.assert_allclose(curve.yData, [result.raw_latency_ms for result in valid_results])
        counts, _ = np.histogram(
            [result.raw_latency_ms for result in valid_results],
            bins=page.distribution_bin_edges,
        )
        np.testing.assert_allclose(page.distribution_bars[comparison.session.id].opts["height"], counts)

    for key, attribute in (
        ("mean", "mean_latency_ms"),
        ("median", "median_latency_ms"),
        ("p95", "p95_latency_ms"),
    ):
        expected_values = [
            getattr(comparison.statistics.raw, attribute) for comparison in page._comparisons
        ]
        np.testing.assert_allclose(page.statistics_bars[key].opts["height"], expected_values)

    assert len(page.invalid_trend_items) == 2
    assert all(item.opts["symbol"] == "x" for item in page.invalid_trend_items)
    assert sorted(float(item.yData[0]) for item in page.invalid_trend_items) == [250.0, 260.0]
    assert page.distribution_bin_edges.size > 1
    assert sum(
        float(np.sum(bar.opts["height"])) for bar in page.distribution_bars.values()
    ) == 4.0
    compensation_row = _condition_row(page, "补偿")
    condition_values = [
        page.conditions_table.item(compensation_row, column).text()
        for column in range(1, 3)
    ]
    assert "2.00 ms（校准配置）" in condition_values
    assert "0.00 ms（未补偿）" in condition_values
    assert "连接方式" in page.condition_difference_label.text()
    assert "补偿" in page.condition_difference_label.text()
    output_row = _condition_row(page, "输出设备")
    assert "模拟输出" in page.conditions_table.item(output_row, 1).text()
    assert "ID：sim-output" in page.conditions_table.item(output_row, 1).text()

    page.metric_combo.setCurrentIndex(page.metric_combo.findData("compensated"))

    for row, comparison in enumerate(page._comparisons):
        expected = comparison.statistics.compensated
        assert page.statistics_table.item(row, 4).text() == f"{expected.mean_latency_ms:.2f} ms"
        valid_results = [result for result in comparison.session.results if result.is_valid]
        np.testing.assert_allclose(
            page.trend_curves[comparison.session.id].yData,
            [result.compensated_latency_ms for result in valid_results],
        )
    np.testing.assert_allclose(
        page.statistics_bars["mean"].opts["height"],
        [comparison.statistics.compensated.mean_latency_ms for comparison in page._comparisons],
    )
    assert "补偿后延迟" in page.analysis_summary_label.text()


def test_comparison_page_all_invalid_sessions_show_empty_charts_not_zero(
    comparison_context, qt_application
):
    service, controller, page = comparison_context
    service.save_session(_all_invalid_snapshot("invalid-first", "Invalid A"))
    service.save_session(_all_invalid_snapshot("invalid-second", "Invalid B"))
    _refresh_sessions(qt_application, controller, page)
    _compare_all(qt_application, controller, page)

    assert page.analysis_scroll.isVisible() is True
    assert page.statistics_bars == {}
    assert page.trend_curves == {}
    assert page.distribution_bars == {}
    assert page.distribution_bin_edges.size == 0
    assert page.statistics_empty_label.isVisible() is True
    assert page.trend_empty_label.isVisible() is True
    assert page.distribution_empty_label.isVisible() is True
    assert page.statistics_plot.getViewBox().viewRange()[1] == [0.0, 1.0]
    for row in range(2):
        assert page.statistics_table.item(row, 2).text() == "0 / 3"
        assert page.statistics_table.item(row, 3).text() == "3"
        for column in (4, 5, 6, 7):
            assert page.statistics_table.item(row, column).text() == "—"
    assert len(page.invalid_trend_items) == 2
    assert all(len(item.yData) == 3 for item in page.invalid_trend_items)
    assert "无有效样本" in page.distribution_note_label.text()
    for comparison in page._comparisons:
        assert len(comparison.session.results) == 3
        assert all(not result.is_valid for result in comparison.session.results)


def test_comparison_page_empty_state_and_background_error_recovery(
    comparison_context, qt_application, monkeypatch
):
    service, controller, page = comparison_context
    _refresh_sessions(qt_application, controller, page)
    assert page.session_table.rowCount() == 0
    assert page.sessions_empty_label.isVisible() is True
    assert page.compare_button.isEnabled() is False
    assert page.analysis_scroll.isVisible() is False
    assert page.analysis_empty_label.isVisible() is True

    _save_two_sessions(service)
    _refresh_sessions(qt_application, controller, page)
    page.session_table.selectAll()

    def fail_comparison(_session_ids):
        raise sqlite3.OperationalError("模拟对比查询失败")

    failures = []
    controller.operation_failed.connect(lambda operation, message: failures.append((operation, message)))
    monkeypatch.setattr(service, "load_comparison_sessions", fail_comparison)
    page.compare_button.click()
    assert _wait_for_signal(controller.operation_finished) is True
    qt_application.processEvents()

    assert failures == [("load_comparison_sessions", "模拟对比查询失败")]
    assert "模拟对比查询失败" in page.status_label.text()
    assert "模拟对比查询失败" in page.analysis_empty_label.text()
    assert page.analysis_scroll.isVisible() is False
    assert page.compare_button.isEnabled() is True

    monkeypatch.undo()
    page.compare_button.click()
    assert _wait_for_signal(controller.comparison_sessions_loaded) is True
    qt_application.processEvents()
    assert page.analysis_scroll.isVisible() is True
    assert len(page._comparisons) == 2
