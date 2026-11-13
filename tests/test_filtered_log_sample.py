import run_collect as rc


def test_first_items_are_logged():
    line = rc.filtered_log_line(1, {"headline": "Acme cuts 50 jobs"}, "why")
    assert line == "  filtered  Acme cuts 50 jobs  (why)"


def test_logging_stops_after_sample():
    n = rc.FILTERED_LOG_SAMPLE
    assert rc.filtered_log_line(n, {"headline": "x"}, "r") is not None
    assert rc.filtered_log_line(n + 1, {"headline": "x"}, "r") is None
