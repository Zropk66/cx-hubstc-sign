import argparse
from sign import has_cli_overrides

def test_has_cli_overrides():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookies", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--address", default=None)
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lng", type=float, default=None)
    parser.add_argument("--photo", default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--bark-device-key", default=None)
    parser.add_argument("--bark-device-token", default=None)

    args_default = parser.parse_args([])
    assert has_cli_overrides(args_default) is False

    args_with_username = parser.parse_args(["--username", "13912345678"])
    assert has_cli_overrides(args_with_username) is True

    args_with_coords = parser.parse_args(["--lat", "30.5", "--lng", "114.4"])
    assert has_cli_overrides(args_with_coords) is True

import shutil
from unittest.mock import patch, MagicMock
from pathlib import Path
import sign

@patch("sign.validate_cookies")
def test_run_sign_in_log_sink_lifecycle(mock_validate_cookies):
    mock_validate_cookies.return_value = True
    sign._args_global = MagicMock()

    temp_log_dir = Path("tests/temp_logs")
    if temp_log_dir.exists():
        shutil.rmtree(temp_log_dir)
    temp_log_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "host": "test.chaoxing.com",
        "username": "test",
        "password": "pwd"
    }

    with patch("sign.fetch_mobile_version") as mock_fetch_v, \
         patch("sign.load_cookies") as mock_load_cookies, \
         patch("sign.requests.Session") as mock_session_cls:

        mock_fetch_v.return_value = "mock_v"
        mock_load_cookies.return_value = {"_uid": "123", "UID": "123"}

        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Mocked stop")
        mock_session_cls.return_value = mock_session

        success = sign.run_sign_in(config, "tests/temp_cookies.txt", str(temp_log_dir))
        assert success is False

        log_files = list(temp_log_dir.glob("*.log"))
        assert len(log_files) > 0

        # Clean up
        shutil.rmtree(temp_log_dir)

