#!/usr/bin/env python3
"""
Can a research agent hear a video?

Runs the same question through the Parallel Task API twice: once with the agent's
own web tools, once with the deAPI MCP server attached. The first run gets the
YouTube navigation bar. The second gets what the speaker actually said.

Two transcript paths are implemented:

  inline    One MCP tool call. The transcript comes back inside the tool result.
            Works up to Parallel's 25,000-character per-result cap, which is
            roughly 20 minutes of speech.

  two-step  Two MCP tool calls. The transcription tool is asked to store the
            result instead of returning it, then check_job_status hands back a
            download URL the agent reads with its own web tools. No size cap.

The two-step path also works around an open deAPI bug: video_url_transcription
drops result_url from its response, so the URL has to be recovered from
check_job_status. See jira-mcp-result-url.md. Once that lands, step 2 goes away.

Requirements: Python 3.9+. No third-party packages.

    export PARALLEL_API_KEY=...
    export DEAPI_API_KEY=...
    python3 demo.py                          # short video, inline path
    python3 demo.py --long                   # 42-minute video, two-step path
    python3 demo.py <youtube-url> "<question>"
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

PARALLEL_BASE = "https://api.parallel.ai"
DEAPI_MCP_URL = "https://mcp.deapi.ai/mcp"

# A 19-second video: the first one ever uploaded to YouTube. Short enough that
# anyone can check the answer by watching it.
SHORT_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
SHORT_QUESTION = "What exactly does the speaker say about the elephants? Quote his words."

# A 42-minute conference talk. Its transcript is ~48,000 characters, which is
# almost twice Parallel's per-result cap - this is what the two-step path is for.
LONG_VIDEO = "https://www.youtube.com/watch?v=bZQun8Y4L2A"
LONG_QUESTION = (
    "What does the speaker say the stages of the GPT training pipeline are? "
    "Quote the list verbatim."
)


# --------------------------------------------------------------------------
# Parallel Task API
# --------------------------------------------------------------------------

def _parallel_request(method, path, api_key, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        PARALLEL_BASE + path,
        data=data,
        method=method,
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        body = error.read().decode() or "{}"
        return error.code, json.loads(body)


def run_task(api_key, question, processor, mcp_servers=None, poll_seconds=1800):
    """Create a task run, wait for it, and return (run, result, seconds)."""
    body = {"input": question, "processor": processor}
    if mcp_servers:
        body["mcp_servers"] = mcp_servers

    started = time.time()
    status, created = _parallel_request("POST", "/v1/tasks/runs", api_key, body)
    if status >= 300:
        raise RuntimeError(f"Task API refused the run ({status}): {json.dumps(created)[:400]}")

    run_id = created["run_id"]
    print(f"    run_id {run_id}")

    state = created.get("status")
    while time.time() - started < poll_seconds:
        time.sleep(3)
        _, run = _parallel_request("GET", f"/v1/tasks/runs/{run_id}", api_key)
        if run.get("status") != state:
            state = run.get("status")
            print(f"    {time.time() - started:6.1f}s  {state}")
        if state in ("completed", "failed", "cancelled"):
            break
    else:
        raise RuntimeError(f"Run {run_id} did not finish within {poll_seconds}s")

    _, result = _parallel_request("GET", f"/v1/tasks/runs/{run_id}/result", api_key)
    return run, result, time.time() - started


# --------------------------------------------------------------------------
# The two prompts
# --------------------------------------------------------------------------

def inline_prompt(url, question):
    return (
        f"Watch {url} and answer this question: {question}\n\n"
        "The spoken content of the video is not present in the page HTML. "
        "Use the deapi MCP server's video_url_transcription tool to obtain the "
        "transcript, then answer from it."
    )


def two_step_prompt(url, question):
    return (
        f"Watch {url} and answer this question: {question}\n\n"
        "The spoken content is not in the page HTML, and this video is long "
        "enough that the transcript will not fit in a single tool result. "
        "Use the deapi MCP server in two steps:\n"
        "1. Call video_url_transcription with return_result_in_response set to "
        "false. It stores the transcript and returns a job_id. Ignore the empty "
        "result field - that is expected on this path.\n"
        "2. Call check_job_status with that job_id. It returns result_url, a "
        "direct link to the full transcript as a text file.\n"
        "Then read result_url with your own web tools and answer from the "
        "transcript. Quote exactly; do not guess."
    )


def deapi_server(deapi_key, tools):
    return [{
        "type": "url",
        "url": DEAPI_MCP_URL,
        "name": "deapi",
        "headers": {"Authorization": f"Bearer {deapi_key}"},
        "allowed_tools": tools,
    }]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def answer_text(result):
    content = (result.get("output") or {}).get("content")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def tool_calls(result):
    return (result.get("output") or {}).get("mcp_tool_calls") or []


def describe_calls(result):
    calls = tool_calls(result)
    if not calls:
        print("    no MCP tool calls")
        return
    for index, call in enumerate(calls, 1):
        arguments = json.loads(call.get("arguments") or "{}")
        content = call.get("content") or ""
        error = call.get("error")
        print(f"    call {index}: {call.get('server_name')}.{call.get('tool_name')}")
        for key in ("video_url", "include_ts", "return_result_in_response", "job_id"):
            if key in arguments:
                print(f"              {key}={arguments[key]}")
        print(f"              returned {len(content)} chars"
              + (f", error={error}" if error else ""))
        if "25,000-character per-result limit" in content:
            print("              NOTE: Parallel truncated this result at its 25,000-char cap")


def heard_the_video(text, url):
    """Crude but honest check: did any spoken word reach the answer?"""
    markers = {
        SHORT_VIDEO: ["elephant", "trunk", "really"],
        LONG_VIDEO: ["pre-training", "fine-tuning", "reward modeling"],
    }.get(url)
    if not markers:
        return None
    lowered = text.lower()
    return sum(1 for marker in markers if marker in lowered), len(markers)


# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compare a Parallel task run with and without the deAPI MCP server.")
    parser.add_argument("url", nargs="?", help="video URL (YouTube, X, Twitch, Kick)")
    parser.add_argument("question", nargs="?", help="what to ask about the video")
    parser.add_argument("--long", action="store_true",
                        help="use the 42-minute example and the two-step path")
    parser.add_argument("--two-step", action="store_true",
                        help="force the two-step path on any video")
    parser.add_argument("--skip-control", action="store_true",
                        help="skip the run without the MCP server")
    args = parser.parse_args()

    parallel_key = os.environ.get("PARALLEL_API_KEY")
    deapi_key = os.environ.get("DEAPI_API_KEY")
    if not parallel_key or not deapi_key:
        sys.exit("Set PARALLEL_API_KEY and DEAPI_API_KEY first. "
                 "Parallel keys: platform.parallel.ai. deAPI keys: app.deapi.ai/dashboard.")

    if args.long:
        url, question = LONG_VIDEO, LONG_QUESTION
    else:
        url = args.url or SHORT_VIDEO
        question = args.question or (SHORT_QUESTION if url == SHORT_VIDEO
                                     else "Summarise what the speaker says, quoting exactly.")

    two_step = args.two_step or args.long

    print(f"\nvideo     {url}")
    print(f"question  {question}")
    print(f"path      {'two-step (stored transcript + download URL)' if two_step else 'inline (single tool call)'}")

    if not args.skip_control:
        print("\n=== 1. Parallel on its own =========================================")
        _, control, seconds = run_task(
            parallel_key,
            f"Watch {url} and answer this question: {question}",
            processor="lite",
        )
        text = answer_text(control)
        print(f"    finished in {seconds:.1f}s")
        describe_calls(control)
        print(f"\n    {text.strip()[:600]}")
        score = heard_the_video(text, url)
        if score:
            print(f"\n    spoken words found: {score[0]} of {score[1]}")

    print("\n=== 2. Parallel with the deAPI MCP server ==========================")
    if two_step:
        # base, not lite: Parallel documents lite as making at most one tool call.
        processor = "base"
        prompt = two_step_prompt(url, question)
        tools = ["video_url_transcription", "check_job_status"]
    else:
        processor = "lite"
        prompt = inline_prompt(url, question)
        tools = ["video_url_transcription"]

    print(f"    processor {processor}, allowed tools: {', '.join(tools)}")
    _, augmented, seconds = run_task(
        parallel_key, prompt, processor=processor,
        mcp_servers=deapi_server(deapi_key, tools),
    )
    text = answer_text(augmented)
    print(f"    finished in {seconds:.1f}s")
    describe_calls(augmented)
    print(f"\n    {text.strip()[:900]}")
    score = heard_the_video(text, url)
    if score:
        print(f"\n    spoken words found: {score[0]} of {score[1]}")

    print("\nCost of this comparison: two Parallel runs plus one transcription.")
    print("Parallel lite is $0.005 per run, base $0.010. deAPI transcription is")
    print("$0.005 + $0.0000130208 per second of video.\n")


if __name__ == "__main__":
    main()
