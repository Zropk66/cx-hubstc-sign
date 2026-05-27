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
