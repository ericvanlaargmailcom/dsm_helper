# Native macOS Cheat Assistant for De Slimste Mens

This helper uses only Python standard library code plus native macOS tools:
`screencapture`, `pbcopy`, `osascript`, `afplay`, and Apple's Vision framework
through Swift.

## Build

```sh
swiftc ocr.swift -o ocr
swiftc keywatch.swift -o keywatch
chmod +x helper.py
```

## Run

For an OpenAI-compatible chat completions endpoint:

```sh
export LLM_API_KEY="your-key"
export LLM_ENDPOINT="https://api.openai.com/v1/chat/completions"
export LLM_MODEL="gpt-4o-mini"
./helper.py
```

Click away from Terminal, then click Terminal again. The helper will open the
native screenshot crosshairs. Select the question area, and the answer or answer
sequence will be copied to the clipboard with sound cues.

Use this continuous mode while playing. Do not add `--once`, because `--once`
is only a single test capture and exits after one question.

If no API key is set in the Terminal session, the helper asks for it securely
and caches it in a user-only temporary file for future starts. Clear it with:

```sh
./helper.py --clear-key-cache
```

To skip the crosshairs and automatically capture the question area in the
`Slimste Mens` window:

```sh
./start_auto.sh
```

`start_auto.sh` is tuned for highscore speed: strong verification, but limited
search and no extra search before tiebreaks.

`start_gpt55.sh` uses paste trigger mode: after you paste an answer with
`Cmd+V`, the helper waits briefly and captures the next question automatically.
macOS may ask for Accessibility permission for Terminal the first time this is
used. Press `Esc` while a scan is running to cancel that scan and wait for the
next `Cmd+V`.

In Puzzel rounds, answers already given are removed from the next OCR prompt and
search query before the model reasons about the remaining grid.
Only answers that appear in a later OCR scan as accepted are treated as
confirmed exclusions; merely suggested puzzle answers stay pending. Web search
is skipped for Puzzel by default because grid association puzzles are usually
hurt more by slow or noisy search context. Use `--puzzle-search` to opt in.

For slower maximum accuracy:

```sh
./start_accuracy.sh
```

If the automatic crop is slightly off on your display, tune it with relative
window ratios:

```sh
./helper.py --capture-mode game-window --game-question-ratios 0.02,0.12,0.96,0.55
```

To capture immediately once:

```sh
./helper.py --once
```

## Provider Options

OpenAI-compatible chat completions are the default:

```sh
./helper.py --provider openai-chat --endpoint https://api.openai.com/v1/chat/completions
```

OpenAI Responses API:

```sh
./helper.py --provider openai-responses --endpoint https://api.openai.com/v1/responses
```

Anthropic Messages API:

```sh
export LLM_API_KEY="your-anthropic-key"
./helper.py --provider anthropic --endpoint https://api.anthropic.com/v1/messages --model claude-3-5-haiku-latest
```

Local endpoints on `localhost` or `127.0.0.1` do not require an API key.

Useful flags:

```sh
./helper.py --terminal-name iTerm2
./helper.py --trigger-mode paste
./helper.py --paste-trigger-delay 2.5
./helper.py --capture-mode game-window
./helper.py --verify-mode smart
./helper.py --verify-mode always
./helper.py --verify-mode never
./helper.py --verify-style independent
./helper.py --no-tiebreak
./helper.py --no-tiebreak-search
./helper.py --no-evidence-check
./helper.py --search-rounds 4
./helper.py --max-context-items 18
./helper.py --conveyor-delay 2.2
./helper.py --sound /System/Library/Sounds/Tink.aiff
./helper.py --ocr-binary ./ocr
./helper.py --no-verify
```

Normal one-answer questions can be verified with an independent second answer.
`start_auto.sh` uses a faster balanced mode. `start_accuracy.sh` gathers more
search context and does candidate-specific search before a tiebreak. The
balanced mode also runs a short evidence check when two model calls agree on an
answer that is not visible in the search context. Use `--verify-mode smart` or
`--verify-mode never` when speed matters more.
