from unittest.mock import call, patch

from app.services.progress import make_milestone_callback


def test_milestones_fire_in_order():
    cb = make_milestone_callback("foo.mp3")
    with patch("app.services.progress.notifier.notify") as mock:
        cb(10, 100, 1.0)
        assert mock.call_count == 0
        cb(25, 100, 2.0)
        assert mock.call_count == 1
        assert "25%" in mock.call_args[0][0]
        cb(50, 100, 4.0)
        assert mock.call_count == 2
        assert "50%" in mock.call_args[0][0]
        cb(75, 100, 6.0)
        assert mock.call_count == 3
        assert "75%" in mock.call_args[0][0]
        cb(99, 100, 8.0)
        assert mock.call_count == 3


def test_does_not_re_fire():
    cb = make_milestone_callback("foo.mp3")
    with patch("app.services.progress.notifier.notify") as mock:
        cb(25, 100, 1.0)
        cb(30, 100, 2.0)
        cb(40, 100, 3.0)
        assert mock.call_count == 1


def test_collapses_simultaneous_crossings():
    cb = make_milestone_callback("foo.mp3")
    with patch("app.services.progress.notifier.notify") as mock:
        cb(80, 100, 1.0)
        assert mock.call_count == 1
        assert "75%" in mock.call_args[0][0]


def test_handles_zero_total():
    cb = make_milestone_callback("foo.mp3")
    with patch("app.services.progress.notifier.notify") as mock:
        cb(0, 0, 0.0)
        assert mock.call_count == 0


def test_eta_displayed_in_body():
    cb = make_milestone_callback("foo.mp3")
    with patch("app.services.progress.notifier.notify") as mock:
        cb(50, 100, 300.0)  # ETA = 300s → "05:00"
        body = mock.call_args[0][1]
        assert "05:00" in body


def test_eta_hms_for_long_files():
    cb = make_milestone_callback("foo.mp3")
    with patch("app.services.progress.notifier.notify") as mock:
        cb(100, 200, 3700.0)  # ETA = 3700s → "01:01:40"
        body = mock.call_args[0][1]
        assert "01:01:40" in body
