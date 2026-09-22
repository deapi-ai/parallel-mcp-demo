# Giving a Parallel task run ears

A Parallel agent can read any page on the web. It cannot hear a video. This demo
measures that gap and closes it with one JSON block — the deAPI MCP server
attached to a Task API run, no code on Parallel's side.

Same video, same question, run twice. Everything below was measured on
2026-08-27 and 2026-09-22 against the live APIs and is reproducible with your
own keys.

## The gap

Ask a task run to quote what someone says in a video and one of two things
happens, depending on how famous the video is.

**Obscure video — the agent says it cannot.** This is the honest failure:

```
Q: What exactly does the speaker say about the elephants? Quote his words.
   (youtube.com/watch?v=jNQXAC9IVRw — "Me at the zoo", 19 seconds)

A: The exact statement about the elephants is not present in the available
   transcript excerpt, so I can't quote it verbatim.
```

Fetching the page returns the navigation bar, a list of recommended videos, and
the word "Transcript" as a button label. The agent knows a transcript exists and
cannot read it.

**Famous video — the agent answers, confidently, and gets it wrong.** This is
the failure that matters:

```
Q: What does the speaker say the stages of the GPT training pipeline are?
   (youtube.com/watch?v=bZQun8Y4L2A — a 42-minute conference talk)

A: "pretraining, supervised finetuning, and Reinforcement Learning from
   Human Feedback (RLHF)."
```

Three stages. Attach the deAPI MCP server, ask the identical question, and the
same run answers from the audio:

```
A: "pre-training, supervised fine-tuning, reward modeling, reinforcement learning"
```

Four stages, verbatim. The control's answer was assembled from secondary
write-ups about the talk rather than from the talk, and it dropped one. It is
plausible, it is cited, and it is not what was said — which is the failure mode
worth caring about, because nothing in the response marks it as wrong.

## What it shows

- A measured control: the same question, same processor, without the MCP server.
- `mcp_servers` wired to a third-party MCP server with bearer auth, end to end.
- Two transcript strategies and when each applies — inline under Parallel's
  per-result cap, handed over as a link above it. Both are one tool call.
- What a tool result actually costs, read from account balance rather than from
  a price endpoint.

## Architecture

```
question ──┬─► task run (lite)                        ──► "I can't quote it"
           │      agent's own web tools → page HTML
           │
           └─► task run (lite) + mcp_servers: deapi   ──► the actual sentence
                  video_url_transcription → Whisper → transcript in the result

above ~20 minutes of speech:

  task run (lite) + mcp_servers: deapi
    1. video_url_transcription(return_result_in_response=false) → result_url
    2. agent reads result_url with its own web tools            → transcript
```

## Quick start

Python 3.9+, no third-party packages.

```bash
export PARALLEL_API_KEY=...    # platform.parallel.ai
export DEAPI_API_KEY=...       # app.deapi.ai/dashboard

python3 demo.py                # 19-second video, transcript inline
python3 demo.py --long         # 42-minute talk, transcript as a link
python3 demo.py "<url>" "<question>"
```

Works on YouTube, X, Twitch, Kick and TikTok. For TikTok the prompt asks the
agent to use `WhisperLargeV3Ct2`, which is cheaper there and returns no text,
rather than invented text, on clips without speech.

## Measured

Current version, 2026-09-22. Every run on `lite`, one MCP call each. The deAPI
balance was read immediately before and after each run, so the cost column is
what was actually charged. It matched the price the tool reported to six
decimal places every time.

| Path | Material | Wall clock | Tool calls | deAPI cost | Outcome |
| --- | --- | --- | --- | --- | --- |
| inline | 19 s | 29.8 s | 1 | $0.005247 | exact quote |
| by-link | 42 min | 114.4 s | 1 | $0.038333 | four stages, verbatim |
| inline | TikTok, < 1 min | 37.1 s | 1 | $0.020000 ‡ | two exact quotes |

‡ $0.015 for the transcription plus a flat $0.005 for `include_metadata`, which
the agent switched on by itself. See Known limits.

The 42-minute talk is the row that matters. It used to need `base` and two
calls, and a single inline call came back truncated. It now runs on `lite` with
one call: the tool returns a link to the transcript, and the agent reads it with
its own web tools.

### Earlier measurements, 2026-08-27

These runs used the previous version of this demo. At that point the long path
needed a second call to `check_job_status`, because the transcription tool did
not return `result_url`. The control rows still hold.

Eight runs against `POST /v1/tasks/runs`. Except where noted, the deAPI balance
was read before and after, so the cost column is what was actually charged, not
what a price endpoint predicted.

| Run | MCP | Material | Wall clock | Tool calls | deAPI cost | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| control | — | 19 s | 32.7 s | 0 | $0 | refuses to quote |
| control | — | 42 min | 26.7 s | 0 | $0 | answers, drops a stage |
| inline | deapi | 19 s | 29.7 s | 1 | $0.005247 | exact quote |
| inline | deapi | 18:40 | 52.7 s | 1 | $0.019583 | two exact quotes |
| inline | deapi | 18:40 | 68.3 s | 1 | $0.019583 | billed 1.00× of quote |
| inline | deapi | 42 min | 204.6 s | 2 | $0.076666 | transcript truncated |
| two-call (old) | deapi | 19 s | 49.1 s | 2 | $0.005247 | exact quote, no size cap |
| two-call (old) | deapi | 42 min | 211.0 s | 2 | $0.038333 † | four stages, verbatim |

† quoted price; this run's balance was not isolated. On every run that was
measured tightly, the charge matched the quote to six decimal places.

Same 42-minute video: inline comes back truncated and costs double because the
agent retries, while the link path returns the whole transcript for one
transcription charge.

Proof the tool ran is in Parallel's own response: `output.mcp_tool_calls` shows
`deapi.video_url_transcription` with an empty `error` field.

## How it works

**`lite` is enough, and that is the point.** Parallel's Browser Use integration
documents `ultra` ($0.30 per run) because a browser task has to be started and
then polled. The deAPI transcription tool polls internally and returns the
transcript in one response, so this runs on `lite` at $0.005 — sixty times
cheaper. Parallel documents that *"for `lite` and `core`, at most one tool is
invoked"*, and both paths here need exactly one MCP call. Anything that needs
more only has to step up to `base` at $0.010, not to `ultra`.

**There is a 25,000-character ceiling on tool results.** Parallel reports it
inside the tool result itself:

```
[truncated: result exceeded the 25,000-character per-result limit;
 23,211 of 48,211 characters were dropped.]
```

That is roughly 20 minutes of speech. Above it, an inline transcript comes back
cut off mid-sentence.

**The fix is not chunking.** The link path asks the transcription tool to store
its output instead of returning it. The tool returns `result_url`, a signed
download link, and the agent reads it with its own web tools. Parallel is very good at turning a URL
into the parts relevant to a question — that is its core product. Handing the
transcript over as a URL plays to that instead of fighting the character cap,
and it removes the length limit entirely rather than raising it.

## Cost

Parallel `lite` $0.005 per run, `base` $0.010. deAPI transcription is
`$0.005 + $0.0000130208 per second`, so 19 seconds costs half a cent and 42
minutes costs four cents. Quoted price matched the actual charge to six decimal
places on every run except the truncated one, which was billed twice because the
agent called the tool twice.

The whole six-run comparison cost $0.035 on Parallel and about $0.13 on deAPI.

## Known limits

- Above ~20 minutes of speech, use the link path (`--long` / `--by-link`).
  Inline gets truncated.
- On the 42-minute inline run (2026-08-27), `lite` made two tool calls,
  despite the documented one-call limit, which doubled the transcription charge.
  The link path avoids this: its result is small and the agent does not retry.
- The agent fills in optional tool parameters on its own. On the TikTok run it
  set `include_metadata: true`, which adds a flat $0.005. If cost matters, say in
  the prompt which options to leave off, or check `mcp_tool_calls[].arguments`.
- TikTok is priced higher than the other platforms, and differently per model.
  Quote it with `video_url_transcription_price` using `duration_seconds` and
  `platform: "tiktok"`; a TikTok URL cannot be quoted directly.

## Related

- [`deapi-ai/mcp-server-deapi`](https://github.com/deapi-ai/mcp-server-deapi) —
  the MCP server this demo attaches, 41 tools across image, video, audio and
  embeddings. Streamable HTTP, bearer auth.
- [docs.deapi.ai/execution-modes-and-integrations/mcp-server](https://docs.deapi.ai/execution-modes-and-integrations/mcp-server)
  — tool reference and client setup.
- [Parallel: MCP tool calls in the Task API](https://docs.parallel.ai/task-api/mcp-tool-call)
  — the `mcp_servers` field used here.

## License

MIT. See [LICENSE](LICENSE).
