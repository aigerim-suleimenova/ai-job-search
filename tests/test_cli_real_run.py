"""The command-line tools, run for real rather than with subprocess stubbed out.

test_clis.py next door stubs subprocess.run and checks what we meant to call the
program with. Here the program is called for real: an executable bearing its name
is put on PATH, it writes down what it was called with and what arrived on stdin,
and it answers.

The difference is not academic. A stub will not show whether the name is on PATH
at all, whether launching trips over Windows (where a .cmd counts as executable
and a file with a hash on its first line does not), or whether the task comes
through the pipe whole — with the newlines, quotes and Cyrillic that encodings
break on.

What even this will not show: whether the real Codex, Copilot, Goose and Qwen
accept the flags we pass them. That is visible only with the programs themselves;
the flags are checked against their documentation, and checking is not running.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from jobsearch import providers

# The task is long on purpose and not in Latin script on purpose: length breaks
# passing it as an argument, Cyrillic breaks the pipe's encoding.
PROMPT = ("Оцени вакансию «Senior Frontend Engineer».\n"
          "Строка с «кавычками», переносом и запятой, вот.\n" * 40)

CALLS = [
    ("codex", lambda: providers.call_codex(PROMPT, "gpt-5-codex", timeout=60), "gpt-5-codex"),
    ("copilot", lambda: providers.call_copilot(PROMPT, "claude-sonnet-4.5", timeout=60),
     "claude-sonnet-4.5"),
    ("goose", lambda: providers.call_goose(PROMPT, "gpt-4o", timeout=60), "gpt-4o"),
    ("qwen", lambda: providers.call_qwen(PROMPT, "qwen3-coder-plus", timeout=60),
     "qwen3-coder-plus"),
]

TRAP = """\
import json, os, sys
beside = os.path.dirname(os.path.abspath(__file__))
name = os.environ.get("AIJS_TRAP_NAME", "none")
task = sys.stdin.read()
with open(os.path.join(beside, name + ".json"), "w", encoding="utf-8") as f:
    json.dump({"argv": sys.argv[1:], "stdin": task}, f, ensure_ascii=False)
sys.stdout.buffer.write(("answer from " + name).encode("utf-8"))
"""


@pytest.fixture
def yard(monkeypatch):
    """A yard with stand-in programs on PATH.

    Not tmp_path: pytest builds it from the test's name, and the names here used
    to be Russian — so the path came out with Cyrillic in it. On Windows that ends
    up inside a .cmd, which is read in the console encoding, and the launch would
    break before it began. The system temporary directory is Latin script.
    """
    import shutil
    import tempfile
    where = Path(tempfile.mkdtemp(prefix="aijs-cli-"))
    trap = where / "trap.py"
    trap.write_text(TRAP, encoding="utf-8")
    for name, _, _ in CALLS:
        if os.name == "nt":
            (where / f"{name}.cmd").write_text(
                f'@echo off\r\nset AIJS_TRAP_NAME={name}\r\n'
                f'"{sys.executable}" "{trap}" %*\r\n', encoding="ascii")
        else:
            path = where / name
            path.write_text(
                f'#!/bin/sh\nAIJS_TRAP_NAME={name} exec "{sys.executable}" "{trap}" "$@"\n',
                encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(where) + os.pathsep + os.environ["PATH"])
    providers.forget_binaries()
    providers.login_env.cache_clear()
    yield where
    providers.forget_binaries()
    providers.login_env.cache_clear()
    shutil.rmtree(where, ignore_errors=True)


@pytest.mark.parametrize("name,call,model", CALLS)
def test_the_program_is_called_and_the_task_arrives(yard, name, call, model):
    answer = call()

    assert answer == f"answer from {name}", "the program's answer was not parsed"
    caught = json.loads((yard / f"{name}.json").read_text(encoding="utf-8"))
    assert caught["stdin"] == PROMPT, "the task came through the pipe mangled"
    assert any(model in a for a in caught["argv"]), "the model name was not passed"
    # The task does not go as an argument and must not: the length of a command
    # line is limited, and prompts like this are exactly where it runs out.
    assert not any(PROMPT[:40] in a for a in caught["argv"])


def test_a_provider_counts_as_installed_when_the_program_is_there(yard):
    """resolve_bin searches PATH, and on Windows that means .cmd — which is what npm
    installs its command-line tools as."""
    available = providers.available("claude", {})
    for name in ("codex_cli", "copilot_cli", "goose_cli", "qwen_cli"):
        assert available[name]["ready"] is True, f"{name} was not found on PATH"


def test_the_programs_complaint_reaches_the_person(yard):
    """The program fails and says why. Swallowing that would leave the person with
    "something went wrong"."""
    if os.name == "nt":
        (yard / "qwen.cmd").write_text(
            "@echo off\r\necho qwen needs an API key 1>&2\r\nexit /b 1\r\n", encoding="ascii")
    else:
        path = yard / "qwen"
        path.write_text('#!/bin/sh\necho "qwen needs an API key" >&2\nexit 1\n', encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    providers.forget_binaries()

    with pytest.raises(providers.ProviderError) as caught:
        providers.call_qwen("hello", "", timeout=60)

    assert "needs an API key" in str(caught.value)


def test_a_silent_failure_is_explained_too(yard):
    """And if the program said nothing, we have to say it — otherwise the person
    sees emptiness."""
    from jobsearch import i18n
    if os.name == "nt":
        (yard / "goose.cmd").write_text("@echo off\r\nexit /b 3\r\n", encoding="ascii")
    else:
        path = yard / "goose"
        path.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    providers.forget_binaries()

    with pytest.raises(providers.ProviderError) as caught:
        providers.call_goose("hello", "", timeout=60)

    text = i18n.err("ru", caught.value)
    assert "goose" in text and "3" in text, f"nothing to read: {text}"


def test_cyrillic_in_the_environment_does_not_break_the_launch(monkeypatch, yard):
    """Found by checking live, and it was a real breakage.

    login_env() asks the login shell for the variables, and used to parse the
    answer with the system encoding — cp1252 on Windows, ascii under the C locale.
    One non-Latin letter in somebody's variable and the parsing collapsed. And
    everyone whose name is not in Latin script has one: it sits in HOME and in
    PATH.

    It failed badly, too: the error happened in the reading thread, stdout
    silently came back None, and what came out was an AttributeError instead of a
    comprehensible refusal. Every command-line tool stopped working at once.

    The shell is one we substitute. The real one differs from machine to machine,
    and on some there is none at all — a test relying on it would run empty,
    checking nothing. This one answers with exactly what we tripped over.
    """
    dump = yard / "envdump.py"
    dump.write_text(
        "import sys" + chr(10) +
        'sys.stdout.buffer.write("HOME=/Users/Виктор' + chr(92) + '0ИМЯ=Лаврентьев'
        + chr(92) + '0".encode("utf-8"))' + chr(10),
        encoding="utf-8")
    if os.name == "nt":
        shell = yard / "shell.cmd"
        shell.write_text("@echo off" + chr(13) + chr(10)
                            + f'"{sys.executable}" "{dump}"' + chr(13) + chr(10),
                            encoding="ascii")
    else:
        shell = yard / "shell.sh"
        shell.write_text("#!/bin/sh" + chr(10)
                            + f'exec "{sys.executable}" "{dump}"' + chr(10),
                            encoding="utf-8")
        shell.chmod(shell.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SHELL", str(shell))
    providers.login_env.cache_clear()

    env = providers.login_env()

    assert env.get("HOME") == "/Users/Виктор", f"the variables did not parse: {env.get('HOME')!r}"
    assert env.get("ИМЯ") == "Лаврентьев"
    # And the call itself runs to the end rather than falling over on the way
    assert providers.call_codex("hello", "", timeout=60) == "answer from codex"


@pytest.mark.skipif(not os.path.exists("/bin/zsh"), reason="needs a real zsh")
def test_a_cli_from_the_nvm_tree_is_found(monkeypatch, tmp_path):
    """This came from a person: "it does not see claude code on my mac".

    That is what the machine of somebody who installed the CLI through npm under
    nvm looks like: the program lives in the nvm tree, and .zshrc is what adds
    that tree to PATH. An application launched from Finder gets the bare system
    PATH — and used to ask the shell with zsh -lc. That was the trouble: -l is a
    login shell but not an interactive one, and .zshrc is read only by an
    interactive shell. Nobody saw the program: not PATH, not the directory list,
    not the shell.

    The name is deliberately one that does not exist: a real claude is on the
    developer's own machine in ~/.local/bin, and a test using it would run empty,
    checking nothing.
    """
    name = "aijs-fake-cli"
    tree = tmp_path / ".nvm" / "versions" / "node" / "v22.17.0" / "bin"
    tree.mkdir(parents=True)
    path = tree / name
    path.write_text("#!/bin/sh\necho 1.0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    (tmp_path / ".zshrc").write_text(
        f'export PATH="{tree}:$PATH"\n', encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ZDOTDIR", raising=False)   # otherwise a .zshrc other than ours is read
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")  # as it is from Finder
    providers.forget_binaries()

    assert providers.resolve_bin(name) == str(path), "the program in the nvm tree was not found"
    # And the environment it will be called with sees the tree too — otherwise it
    # is found but will not run: node, which it lives by, sits in that same
    # directory beside it.
    providers.login_env.cache_clear()
    assert str(tree) in providers.login_env().get("PATH", "")
    providers.forget_binaries()
    providers.login_env.cache_clear()


@pytest.mark.skipif(os.name == "nt", reason="the stand-in shell here is an sh script")
def test_a_chatty_zshrc_does_not_spoil_the_variables(monkeypatch, yard):
    """The price of an interactive shell: .zshrc prints things of its own, and prints
    them before env is reached. Its words stick to the very first variable — and
    that first one can be any of them, PATH included."""
    dump = yard / "chatty.py"
    dump.write_text(
        "import sys" + chr(10) +
        'sys.stdout.buffer.write("Добро пожаловать!' + chr(92) + 'n'
        'PATH=/из/оболочки' + chr(92) + '0ИМЯ=Лаврентьев' + chr(92) + '0".encode("utf-8"))'
        + chr(10),
        encoding="utf-8")
    shell = yard / "shell.sh"
    shell.write_text("#!/bin/sh" + chr(10) + f'exec "{sys.executable}" "{dump}"' + chr(10),
                        encoding="utf-8")
    shell.chmod(shell.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SHELL", str(shell))
    providers.login_env.cache_clear()

    env = providers.login_env()

    assert env.get("PATH") == "/из/оболочки", f"the greeting stuck to it: {env.get('PATH')!r}"
    assert env.get("ИМЯ") == "Лаврентьев"
    providers.login_env.cache_clear()
