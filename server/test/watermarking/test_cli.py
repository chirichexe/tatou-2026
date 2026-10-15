"""Watermarking CLI behavior and error sanitization."""

import builtins
import io
import json
import sys
from types import SimpleNamespace

import pytest

import watermarking_cli as cli
from watermarking_method import (
    InvalidKeyError,
    SecretNotFoundError,
    WatermarkingError,
)

SENSITIVE_CANARY = "secret-canary-do-not-disclose"


def test_read_text_from_stdin_rejects_empty_input(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    with pytest.raises(ValueError):
        cli._read_text_from_stdin()


def test_explicit_secret_does_not_prompt(monkeypatch):
    args = SimpleNamespace(
        secret="explicit-secret",
        secret_file=None,
        secret_stdin=False,
    )

    def unexpected_prompt(_message):
        pytest.fail("Unexpected secret prompt")

    monkeypatch.setattr(cli.getpass, "getpass", unexpected_prompt)

    assert cli._resolve_secret(args) == "explicit-secret"


@pytest.mark.parametrize("key_source", ["argument", "file", "stdin", "prompt"])
@pytest.mark.parametrize("key_content", ["reader-key", "reader-key-caffè\r\n"])
def test_extract_verifies_without_revealing_secret(
    monkeypatch, capsys, tmp_path, key_source, key_content,
):
    calls = []

    def read_watermark(**kwargs):
        calls.append(kwargs)
        return SENSITIVE_CANARY

    monkeypatch.setattr(cli, "read_watermark", read_watermark)

    def prompt(_message):
        assert key_source == "prompt", "Unexpected interactive key prompt"
        return key_content

    monkeypatch.setattr(cli.getpass, "getpass", prompt)

    if key_source == "file":
        path = tmp_path / "key.txt"
        path.write_bytes(key_content.encode("utf-8"))

        def ascii_default_open(*args, **kwargs):
            if kwargs.get("encoding") is None:
                kwargs["encoding"] = "ascii"
            return builtins.open(*args, **kwargs)

        monkeypatch.setattr(cli, "open", ascii_default_open, raising=False)
        key_option = ["--key-file", str(path)]
    elif key_source == "stdin":
        monkeypatch.setattr(sys, "stdin", io.StringIO(key_content))
        key_option = ["--key-stdin"]
    elif key_source == "prompt":
        key_option = ["--key-prompt"]
    else:
        key_option = ["--key", key_content]

    expected_key = key_content.strip("\n\r") if key_source in {"file", "stdin"} else key_content
    result = cli.main([
        "extract", "marked.pdf", "--method", "toy-eof", *key_option,
    ])

    output = capsys.readouterr()
    assert result == 0
    assert output.out == "Watermark verified\n"
    assert output.err == ""
    assert SENSITIVE_CANARY not in output.out + output.err
    assert expected_key not in output.out + output.err
    assert calls == [{
        "method": "toy-eof", "pdf": "marked.pdf", "key": expected_key,
    }]


@pytest.mark.parametrize("key_option", [
    ["--key", "reader-key"],
    ["--key-file", "key.txt"],
    ["--key-stdin"],
    ["--key-prompt"],
])
def test_extract_parser_preserves_key_options(key_option):
    args = cli.build_parser().parse_args([
        "extract", "marked.pdf", "--method", "toy-eof", *key_option,
    ])

    assert args.cmd == "extract"
    assert args.input == "marked.pdf"
    assert args.method == "toy-eof"


def test_extract_rejects_removed_output_option(tmp_path, capsys):
    output_path = tmp_path / f"{SENSITIVE_CANARY}.txt"

    with pytest.raises(SystemExit) as error:
        cli.main([
            "extract", "marked.pdf", "--key", "reader-key",
            "--out", str(output_path),
        ])

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert not output_path.exists()
    assert SENSITIVE_CANARY not in captured.out + captured.err
    assert captured.err.endswith("pdfwm: error: invalid arguments\n")


@pytest.mark.parametrize("exception,status,message", [
    (SecretNotFoundError(SENSITIVE_CANARY), 3, "secret not found\n"),
    (InvalidKeyError(SENSITIVE_CANARY), 4, "invalid key\n"),
    (WatermarkingError(SENSITIVE_CANARY), 5, "watermarking failed\n"),
])
def test_extract_errors_do_not_reveal_exception_text(
    monkeypatch, capsys, exception, status, message,
):
    def fail_read(**_kwargs):
        raise exception

    monkeypatch.setattr(cli, "read_watermark", fail_read)

    result = cli.main(["extract", "marked.pdf", "--key", SENSITIVE_CANARY])

    output = capsys.readouterr()
    assert result == status
    assert output.out == ""
    assert output.err == message
    assert SENSITIVE_CANARY not in output.out + output.err


@pytest.mark.parametrize("exception", [
    FileNotFoundError(f"missing /private/{SENSITIVE_CANARY}.pdf"),
    PermissionError(f"denied /private/{SENSITIVE_CANARY}.pdf"),
    ValueError(f"invalid key {SENSITIVE_CANARY}"),
    KeyError(f"unknown method {SENSITIVE_CANARY}"),
    EOFError(SENSITIVE_CANARY),
])
def test_input_errors_do_not_reveal_exception_text(
    monkeypatch, capsys, exception,
):
    def fail_explore(_path):
        raise exception

    monkeypatch.setattr(cli, "explore_pdf", fail_explore)

    result = cli.main(["explore", f"/private/{SENSITIVE_CANARY}.pdf"])

    output = capsys.readouterr()
    assert result == 2
    assert output.out == ""
    assert output.err == "invalid input\n"
    assert SENSITIVE_CANARY not in output.out + output.err


@pytest.mark.parametrize("exception", [
    RuntimeError(SENSITIVE_CANARY),
    TypeError(SENSITIVE_CANARY),
])
def test_unexpected_processing_errors_do_not_reveal_exception_text(
    monkeypatch, capsys, exception,
):
    def fail_explore(_path):
        raise exception

    monkeypatch.setattr(cli, "explore_pdf", fail_explore)

    result = cli.main(["explore", "document.pdf"])

    output = capsys.readouterr()
    assert result == 5
    assert output.out == ""
    assert output.err == "watermarking failed\n"
    assert SENSITIVE_CANARY not in output.out + output.err


def test_explore_fallback_log_does_not_reveal_exception_text(
    monkeypatch, capsys, caplog, tmp_path,
):
    input_path = tmp_path / "document.pdf"
    input_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    def fail_open(**_kwargs):
        raise RuntimeError(SENSITIVE_CANARY)

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=fail_open))

    result = cli.main(["explore", str(input_path)])

    output = capsys.readouterr()
    assert result == 0
    assert json.loads(output.out)["type"] == "Document"
    assert SENSITIVE_CANARY not in output.out + output.err + caplog.text


def test_non_applicable_embed_does_not_reveal_arguments(
    monkeypatch, capsys, tmp_path,
):
    output_path = tmp_path / f"{SENSITIVE_CANARY}.pdf"
    monkeypatch.setattr(
        cli, "is_watermarking_applicable", lambda **_kwargs: False,
    )

    result = cli.main([
        "embed", "document.pdf", str(output_path),
        "--method", SENSITIVE_CANARY,
        "--position", SENSITIVE_CANARY,
        "--secret", SENSITIVE_CANARY,
        "--key", SENSITIVE_CANARY,
    ])

    output = capsys.readouterr()
    assert result == 5
    assert output.out == ""
    assert output.err == "watermarking failed\n"
    assert SENSITIVE_CANARY not in output.out + output.err


def test_help_describes_safe_extract(capsys):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["--help"])

    output = capsys.readouterr()
    assert error.value.code == 0
    assert "extract" in output.out
    assert "Verify a watermark without revealing its secret" in output.out
    assert "--out" not in output.out


def test_methods_command_still_lists_registered_methods(capsys):
    assert cli.main(["methods"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.splitlines() == ["davide-watermark", "francesco-watermark", "group13-watermark",
                                        "khaled-text-spacing-watermark"]


def test_explore_command_still_outputs_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "explore_pdf", lambda _path: {"type": "Document"})

    assert cli.main(["explore", "document.pdf"]) == 0

    output = capsys.readouterr()
    assert json.loads(output.out) == {"type": "Document"}
    assert output.err == ""


def test_embed_command_does_not_echo_secret_or_key(monkeypatch, tmp_path, capsys):
    output_path = tmp_path / "marked.pdf"
    monkeypatch.setattr(cli, "is_watermarking_applicable", lambda **_kwargs: True)
    monkeypatch.setattr(cli, "apply_watermark", lambda **_kwargs: b"%PDF-safe")

    assert cli.main([
        "embed", "document.pdf", str(output_path),
        "--secret", SENSITIVE_CANARY, "--key", SENSITIVE_CANARY,
    ]) == 0

    output = capsys.readouterr()
    assert output_path.read_bytes() == b"%PDF-safe"
    assert SENSITIVE_CANARY not in output.out + output.err
