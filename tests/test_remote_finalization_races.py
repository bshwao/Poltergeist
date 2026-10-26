"""Cancellation and finalization order through actual SQLite transactions."""

import pytest

from finalization_faults import check_crash_boundary, check_writer_order


@pytest.mark.parametrize("winner", ["finalization", "cancellation"])
def test_cancel_and_finalization_writers_keep_their_order_without_cancelling_next_run(tmp_path, monkeypatch, winner):
    check_writer_order(tmp_path, monkeypatch, winner)


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
def test_actual_process_loss_around_final_result_commit_preserves_receipts_and_waiter(tmp_path, boundary):
    check_crash_boundary(tmp_path / "finalizer", boundary)
