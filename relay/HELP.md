# Relay

Relay manages what a coding agent's model sees. It sits between the agent (Codex, Claude Code,
Gemini CLI, pi, OpenCode and others: a "harness") and the model's API, and on every request
its strategy decides the context: Codex's compaction, a context the model edits itself (CLM),
and more. The harness, its login and its provider stay as they are.

## Setting it up

    relay setup codex --strategy clm

This does three things, in order:

1. It saves the strategy (and any `--set name=value`) in Relay's settings.
2. It starts Relay as a service of yours, so it runs at every login and again if it stops.
3. Once Relay answers, it points the harness at it. `relay install` edits only the harness's
   own config file, and records the old values.

If Relay does not start, the harness is left untouched. The harness names are `codex`,
`claude`, `gemini_cli`, `pi`, `opencode`, `kilo`, `crush`, `openclaw`, `goose`, `kimi_code`,
`hermes`, `nanobot`, `dsh`, `workbuddy` and `mini`.

## Changing the strategy

    relay strategy                 # the strategy now, and the strategies there are
    relay strategy folding         # use Context Folding from now on
    relay strategy clm --set clm_budget=40000

The running Relay picks the change up with the next request, for every conversation it serves,
including the ones in progress. Each conversation starts under the new strategy from its full
history as the harness has it. Switching back finds the earlier strategy's context again.

## Which conversations change

| When | What changes |
| --- | --- |
| Relay already set up, strategy changed | Every conversation, from its next request |
| First `relay setup` | New conversations. Codex, Claude Code and the other harnesses read their config as a conversation starts: one already open goes on as it was, without Relay, until a new one is started. `codex exec resume` starts again on every turn, so it changes from its next turn. |
| `relay uninstall <harness>` | New conversations go to the provider directly again. One already open keeps going through Relay, which keeps running until `relay stop`. |

## For a coding agent asked to set Relay up

1. Install the package, for example `pip install <its source>`. This needs network access.
2. Run `relay setup <your harness> --strategy <name>` outside your sandbox: it writes the
   harness's config in the home directory and starts a service. Ask the user to approve it
   (in Codex, a command that runs with escalated permissions).
3. Run `relay status` and check that it reports the strategy.
4. Tell the user that the strategy applies to conversations started from now on, and that
   this one goes on as before. If Relay was already set up, the change applies from the next
   request. Do not restart the harness or its app server: that can drop its connections.

To change the strategy later, run `relay strategy <name>`. It needs no restart.

## Strategies

| Strategy | What the model sees | Settings |
| --- | --- | --- |
| `compaction` (default) | Codex's local compaction: past a threshold, a summary and the recent user messages | `compact_ratio` (0.9 of the window), `compact_threshold`, `compact_growth`, `retain_user_tokens` (20000), `compact_min_gain` (0.1) |
| `clm` | Context Language Models: the model edits its own context in a file, with its own tools | `clm_budget`, `clm_reserve` (2048), `clm_dir` (/tmp/.live_ctx, a place the harness can write), `clm_nudges` (on), `clm_steering` |
| `folding` | Context Folding: the agent branches off a sub-task, and the branch's steps fold away when it returns | |
| `prolong` | PRO-LONG: compaction, plus a session log the agent can search | `prolong_log` (/tmp/.prolong/log.jsonl) |
| `selfcompact` | Self-Compact: a rubric decides when the model summarizes its own trajectory | |
| `autocompact` | AutoCompact: the model decides when to compact | the `compaction` settings, as its fallback |
| `acm` | ACM: the model compresses its context when it decides to, and recalls what it compressed | `acm_dir` (/tmp/.acm) |
| `mem1` | MEM1: the model carries its memory in a state it rewrites every turn | |
| `rlm`, `rlm_persistent` | RLM: the conversation becomes a variable in a Python REPL the model works over (runs model-written code) | `rlm_environment` (local), `rlm_max_depth` (1), `rlm_max_iterations` (30) |

The thresholds count the model's context window. Relay knows the windows of common models;
for another one, set `context_window`.

## Settings

    relay settings                 # every setting written
    relay set context_window=200000 clm_budget=40000
    relay unset clm_budget

Values are JSON where they parse as JSON (`26000`, `true`), text otherwise. Most settings apply
from the next request. Some only apply when Relay starts, and `relay set` restarts the service
for them:

- where Relay listens: `host` (127.0.0.1), `port` (8787);
- its upstreams: `openai_base_url`, `chatgpt_base_url`, `anthropic_base_url`, `gemini_base_url`,
  and `openai_api_key`, `anthropic_api_key` for requests that carry no key;
- its store: `cache_path` (off keeps it in memory), `cache_ttl_seconds`, `cache_max_entries`,
  `cache_max_bytes`.

## Checking and undoing

    relay status                   # running?, its strategy, what keeps it running, the harnesses set up
    relay uninstall codex          # put the harness's config back as it was
    relay stop                     # stop Relay and remove its service

While a harness points at Relay, it reaches its model only through Relay. If a model request
fails because nothing answers at Relay's address, run `relay status`, read the log
(`~/.config/relay/relay.log`), and `relay setup` again, or `relay uninstall` the harness.

## Other commands

    relay serve                    # run Relay in the foreground (what the service runs)
    relay install <harness>        # only point a harness at Relay (`--via hook`: Claude Code's hook)
    relay run codex -- <args>      # one run through a private, temporary Relay
