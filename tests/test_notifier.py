from unittest.mock import call, patch

from app.services.notifier import notify


def test_notify_calls_osascript():
    with patch("subprocess.run") as mock_run:
        notify("タイトル", "本文")
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args[0] == "osascript"
    assert "display notification" in args[2]
    assert "本文" in args[2]
    assert "タイトル" in args[2]


def test_notify_escapes_double_quotes():
    with patch("subprocess.run") as mock_run:
        notify('ti"tle', 'mes"sage')
    script = mock_run.call_args[0][0][2]
    assert '\\"' in script


def test_notify_silences_file_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        notify("x", "y")  # must not raise
