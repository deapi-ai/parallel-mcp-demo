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
- The transcript delivered inside the tool result, in one call on `lite`, and
  where that stops: Parallel's 25,000-character per-result cap.
- What a tool result actually costs, read from the account balance rather than
  from a price endpoint.

## Architecture

```
question ──┬─► task run (lite)                        ──► "I can't quote it"
           │      agent's own web tools → page HTML
           │
           └─► task run (lite) + mcp_servers: deapi   ──► the actual sentence
                  video_url_transcription → Whisper → transcript in the result

above ~20 minutes of speech Parallel truncates the tool result at 25,000
characters, so only the first ~20 minutes reach the agent (see Known limits).
```

## Quick start

Python 3.9+, no third-party packages.

```bash
export PARALLEL_API_KEY=...    # platform.parallel.ai
export DEAPI_API_KEY=...       # app.deapi.ai/dashboard

python3 demo.py                # 19-second video, transcript inline
python3 demo.py --long         # 42-minute talk, answer is in the first minutes
python3 demo.py "<url>" "<question>"
```

Works on YouTube, X, Twitch, Kick and TikTok. For TikTok the prompt asks the
agent to use `WhisperLargeV3Ct2`, which is cheaper there and returns no text,
rather than invented text, on clips without speech.

## Measured

Current version, 2026-09-22, all on `lite`. The deAPI balance was read
immediately before and after each run, so the cost column is what was actually
charged. Every individual tool call was billed at exactly the price it reported.

| Path | Material | Wall clock | Tool calls | deAPI cost | Outcome |
| --- | --- | --- | --- | --- | --- |
| inline | 19 s | 29.8 s | 1 | $0.005247 | exact quote |
| inline | TikTok, < 1 min | 37.1 s | 1 | $0.020000 ‡ | two exact quotes |
| inline | 42 min | 246.4 s | 1 listed | $0.076666 § | four stages, verbatim; result truncated at 25,000 chars |
| link | 34 min, obscure | 85.2 s | 2 | $0.067656 | **no answer — link redacted by Parallel** |

‡ $0.015 for the transcription plus a flat $0.005 for `include_metadata`, which
the agent switched on by itself. See Known limits.

§ Twice the quoted $0.038333, although `mcp_tool_calls` lists a single call.

On the 42-minute talk the four stages are named in the first minutes, so they
survive the truncation and the tool result contains them. The last row is the
limit: see Known limits.

### Earlier measurements, 2026-08-27

These runs used the previous version of this demo. At that point the long path
needed a second call to `check_job_status`, because the transcription tool did
not return `result_url`. The control rows still hold. The two `two-call` rows do
not: Parallel redacts the transcript link before the agent sees it, and both
videos have transcripts published elsewhere on the web, so those answers most
likely came from those copies rather than from the link.

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

On the 42-minute video, the inline transcript came back truncated and was billed
twice because the agent called the tool twice.

Proof the tool ran is in Parallel's own response: `output.mcp_tool_calls` shows
`deapi.video_url_transcription` with an empty `error` field.

## How it works

**`lite` is enough, and that is the point.** Parallel's Browser Use integration
documents `ultra` ($0.30 per run) because a browser task has to be started and
then polled. The deAPI transcription tool polls internally and returns the
transcript in one response, so this runs on `lite` at $0.005 — sixty times
cheaper. Parallel documents that *"for `lite` and `core`, at most one tool is
invoked"*, and this needs exactly one MCP call. Anything that needs more only has
to step up to `base` at $0.010, not to `ultra`.

**There is a 25,000-character ceiling on tool results.** Parallel reports it
inside the tool result itself:

```
[truncated: result exceeded the 25,000-character per-result limit;
 23,211 of 48,211 characters were dropped.]
```

That is roughly 20 minutes of speech. Above it, an inline transcript comes back
cut off mid-sentence.

**A download link does not get past Parallel.** The transcription tool can store
its output and return `result_url`, a signed link to the full transcript, instead
of the text. Parallel replaces that value with `[redacted]` before the agent sees
it. On a 34-minute meetup recording for which we found no published transcript, the
run's own reasoning says the tool result *"contains only redacted processing
metadata and no spoken transcript text"*, and it does not answer. `--by-link`
reproduces this.

## Cost

Parallel `lite` $0.005 per run, `base` $0.010. deAPI transcription of a YouTube
video is `$0.005 + $0.0000130208 per second`, so 19 seconds costs half a cent
and 42 minutes costs four cents. Other platforms are priced differently; the
`video_url_transcription_price` tool quotes any of them. Each transcription was
billed at exactly its quoted price. A run in which the agent transcribes twice
is billed twice.

The runs in the 2026-09-22 table, plus one control run, cost $0.025 on Parallel
at list price and $0.169569 on deAPI.

## Known limits

- **Only the first ~20 minutes of speech reach the agent.** Parallel cuts tool
  results at 25,000 characters and redacts the signed link that would carry the
  rest. Questions about later parts of a long video go unanswered, or get
  answered from other sources. Reading the full transcript needs a change on
  one side or the other: an unsigned link from the tool, or no redaction of it
  in Parallel.
- `lite` sometimes calls the tool twice, despite the documented one-call limit,
  and each call is billed. It happened on the 42-minute run on 2026-08-27 and on
  the link run on 2026-09-22. On the 42-minute run on 2026-09-22 the charge was
  also doubled, although `mcp_tool_calls` lists only one call.
- The `basis` citations are not a reliable record of where an answer came from.
  On the 42-minute talk the tool result contained the answer, but the only
  citation is a copy of the transcript on GitHub.
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
