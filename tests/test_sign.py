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


import json
from unittest.mock import patch, MagicMock, call
import sign

def test_main_runs_multiple_accounts(tmp_path):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()

    user1_dir = configs_dir / "user1"
    user1_dir.mkdir()
    user1_cfg = {"username": "user1", "host": "host1"}
    with open(user1_dir / "config.json", "w") as f:
        json.dump(user1_cfg, f)

    user2_dir = configs_dir / "user2"
    user2_dir.mkdir()
    user2_cfg = {"username": "user2", "host": "host2"}
    with open(user2_dir / "config.json", "w") as f:
        json.dump(user2_cfg, f)

    with patch("sign.run_sign_in") as mock_run_sign_in, \
         patch("sign.has_cli_overrides") as mock_cli_overrides, \
         patch("os.path.exists") as mock_exists, \
         patch("os.getcwd") as mock_getcwd, \
         patch("pathlib.Path.is_dir") as mock_is_dir, \
         patch("pathlib.Path.iterdir") as mock_iterdir, \
         patch("time.sleep") as mock_sleep:

        mock_cli_overrides.return_value = False

        def exists_side_effect(path):
            if "config.json" in str(path) and "configs" not in str(path):
                return True
            return False
        mock_exists.side_effect = exists_side_effect
        mock_getcwd.return_value = str(tmp_path)

        mock_is_dir.return_value = True
        mock_iterdir.return_value = [user1_dir, user2_dir]

        # Mock file opens inside main() for config reading
        with patch("builtins.open", create=True) as mock_open:
            mock_file_root = MagicMock()
            mock_file_root.read.return_value = '{"username": "main_user"}'
            mock_file_u1 = MagicMock()
            mock_file_u1.read.return_value = json.dumps(user1_cfg)
            mock_file_u2 = MagicMock()
            mock_file_u2.read.return_value = json.dumps(user2_cfg)

            mock_open.side_effect = lambda path, *args, **kwargs: {
                str(tmp_path / "config.json"): mock_file_root,
                str(user1_dir / "config.json"): mock_file_u1,
                str(user2_dir / "config.json"): mock_file_u2,
            }.get(str(path), MagicMock())

            mock_args = MagicMock()
            mock_args.cookies = None

            with patch("argparse.ArgumentParser.parse_args", return_value=mock_args):
                sign.main()

                assert mock_run_sign_in.call_count == 3
                mock_run_sign_in.assert_has_calls([
                    call({"username": "main_user"}, "cookies.txt", "logs"),
                    call(user1_cfg, str(user1_dir / "cookies.txt"), str(user1_dir / "logs")),
                    call(user2_cfg, str(user2_dir / "cookies.txt"), str(user2_dir / "logs"))
                ], any_order=False)

                assert mock_sleep.call_count == 2


