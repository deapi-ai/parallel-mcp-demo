# Giving a Parallel task run ears

A Parallel agent can read any page on the web. It cannot hear a video. This demo
measures that gap and closes it with one JSON block — the deAPI MCP server
attached to a Task API run, no code on Parallel's side.

Same video, same question, run twice. Everything below was measured on
2026-08-27 against the live API and is reproducible with your own keys.

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
  per-result cap, stored-and-fetched above it.
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

  task run (base) + mcp_servers: deapi
    1. video_url_transcription(return_result_in_response=false) → job_id
    2. check_job_status(job_id)                                 → result_url
    3. agent reads result_url with its own web tools            → transcript
```

## Quick start

Python 3.9+, no third-party packages.

```bash
export PARALLEL_API_KEY=...    # platform.parallel.ai
export DEAPI_API_KEY=...       # app.deapi.ai/dashboard

python3 demo.py                # 19-second video, single tool call
python3 demo.py --long         # 42-minute talk, two tool calls
python3 demo.py "<url>" "<question>"
```

Works on YouTube, X, Twitch and Kick.

## Measured

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
| two-step | deapi | 19 s | 49.1 s | 2 | $0.005247 | exact quote, no size cap |
| two-step | deapi | 42 min | 211.0 s | 2 | $0.038333 † | four stages, verbatim |

† quoted price; this run's balance was not isolated. On every run that was
measured tightly, the charge matched the quote to six decimal places.

The last two rows are the interesting ones. Same 42-minute video: inline comes
back truncated and costs double because the agent retries, while the two-step
path returns the whole transcript for one transcription charge.

Proof the tool ran is in Parallel's own response: `output.mcp_tool_calls` shows
`deapi.video_url_transcription` with an empty `error` field.

## How it works

**`lite` is enough, and that is the point.** Parallel's Browser Use integration
documents `ultra` ($0.30 per run) because a browser task has to be started and
then polled. The deAPI transcription tool polls internally and returns the
transcript in one response, so this runs on `lite` at $0.005 — sixty times
cheaper. Worth being precise about the fallback, though: Parallel documents that
*"for `lite` and `core`, at most one tool is invoked. For all other processors,
multiple tool calls may be made"*, so anything needing two calls only has to
step up to `base` at $0.010, not to `ultra`.

**There is a 25,000-character ceiling on tool results.** Parallel reports it
inside the tool result itself:

```
[truncated: result exceeded the 25,000-character per-result limit;
 23,211 of 48,211 characters were dropped.]
```

That is roughly 20 minutes of speech. Above it, an inline transcript comes back
cut off mid-sentence.

**The fix is not chunking.** The two-step path asks the transcription tool to
store its output instead of returning it, then recovers a download URL and lets
the agent read it with its own web tools. Parallel is very good at turning a URL
into the parts relevant to a question — that is its core product. Handing the
transcript over as a URL plays to that instead of fighting the character cap,
and it removes the length limit entirely rather than raising it.

**Why the two-step path needs two calls today.** It should need one. The deAPI
transcription tools accept `return_result_in_response: false` but drop
`result_url` when building their response, so the call returns
`{"success": true, "result": null}` and no way to reach the stored file. The URL
is recovered with a second call to `check_job_status`, which does return it.
This is a known bug in
[`deapi-ai/mcp-server-deapi`](https://github.com/deapi-ai/mcp-server-deapi), not
a property of the design: `PollingManager` collects `result_url`, the tool
function just does not copy it into the dict it returns. When that is fixed,
step 2 disappears and the long path runs on `lite` like the short one.

## Cost

Parallel `lite` $0.005 per run, `base` $0.010. deAPI transcription is
`$0.005 + $0.0000130208 per second`, so 19 seconds costs half a cent and 42
minutes costs four cents. Quoted price matched the actual charge to six decimal
places on every run except the truncated one, which was billed twice because the
agent called the tool twice.

The whole six-run comparison cost $0.035 on Parallel and about $0.13 on deAPI.

## Known limits

- Above ~20 minutes of speech, use the two-step path. Inline gets truncated.
- The `lite` processor made two tool calls on the 42-minute run, contrary to the
  documented one-call limit, doubling the transcription charge. Budget for a
  multiplier on long material until the retry behaviour is understood.
- TikTok is not reachable through the deAPI MCP server yet: the tool advertises
  YouTube, X, Twitch and Kick, and the model that handles TikTok is not exposed
  in the MCP catalogue.

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
