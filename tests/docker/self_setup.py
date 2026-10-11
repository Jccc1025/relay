"""Codex sets Relay up for itself, told in one sentence, as a user would in Remote Control.

    RELAY_TEST_KEYS=keys.env python tests/docker/self_setup.py [codex:gpt | codex:gpt-api] [--strategy clm]

In the image, on a user's machine where nothing of Relay runs: turn 1 asks Codex to install Relay
from /relay and set it up for itself with the strategy, nothing more (`relay help` is how it
finds out). Then a new conversation does a small task. Checks: Relay runs the strategy, Codex's
configuration points at Relay, the new conversation's requests went through Relay and the
strategy acted on them (CLM and Folding rewrite every request; CLM keeps a context file), and the
answer is right.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check import SPECS, container  # noqa: E402

ASK = ("Install the Relay package from /relay (a local checkout) and set it up for yourself, Codex, "
       "with the {strategy} strategy. Its `relay help` tells you how. Then tell me what you did.")
TASK = "Read file_1.txt in full and reply with the value on its line starting with CODE:, nothing else."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", nargs="?", default="codex:gpt", choices=["codex:gpt", "codex:gpt-api"])
    parser.add_argument("--strategy", default="clm")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="relay-self-setup-"))
    (root / "home").mkdir()
    (root / "project").mkdir()
    (root / "project" / "file_1.txt").write_text("".join(f"line {n}: filler\n" for n in range(40)) + "CODE: quartz\n")
    turns = f"""
        cd /project
        export PROMPT={shlex.quote(ASK.format(strategy=options.strategy))}
        timeout 900 {SPECS[options.name].command} > ~/turn1.out 2> ~/turn1.err < /dev/null
        python3 -m relay.cli status > ~/status.out 2>&1
        export PROMPT={shlex.quote(TASK)}
        timeout 600 {SPECS[options.name].command} > ~/turn2.out 2> ~/turn2.err < /dev/null
        ls -1 /tmp/.live_ctx > ~/clm_files.out 2>&1
        python3 -m relay.cli stop >> ~/status.out 2>&1
    """
    container(options.name, root, {}, turns, install=False, serve=False)
    home = root / "home"
    read = lambda name: (home / name).read_text(errors="replace") if (home / name).exists() else ""  # noqa: E731
    trace = [json.loads(line) for line in read("trace.jsonl").splitlines() if line.strip()]
    task = [r for r in trace if "items" in r and TASK[:40] in json.dumps(r.get("body"))]
    config = read(".codex/config.toml")
    problems = [what for what, bad in [
        (f"Relay does not run {options.strategy}: {read('status.out')[:300]!r}", f"strategy {options.strategy}" not in read("status.out")),
        ("Codex's configuration does not point at Relay", "127.0.0.1:8787/up/codex-" not in config),
        ("the new conversation did not go through Relay", not task),
        ("CLM wrote no context file", options.strategy == "clm" and not read("clm_files.out").strip()),
        ("the strategy did not act on the new conversation",  # (both add their guidance to every request)
         options.strategy in ("clm", "folding") and not any(r.get("rewritten") for r in task)),
        (f"the answer is wrong: {read('turn2.out')[-200:]!r}", "quartz" not in read("turn2.out").lower()),
    ] if bad]
    print(f"== {options.name} sets Relay up ({options.strategy}): {'PASS' if not problems else 'FAIL'}  ({home})")
    print(f"  turn 1 said: {read('turn1.out').strip()[-600:]}")
    for problem in problems:
        print(f"  problem: {problem}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
