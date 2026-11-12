import pytest

from scripts.run_calibration import build_argument_parser


def test_calibration_cli_once_option():
    args = build_argument_parser().parse_args(["--once"])

    assert args.once is True


def test_calibration_cli_help_exits_without_running():
    with pytest.raises(SystemExit) as exc_info:
        build_argument_parser().parse_args(["--help"])

    assert exc_info.value.code == 0
