from src.app import format_name


def test_format_name() -> None:
    assert format_name(" codemedic ") == "Codemedic"
