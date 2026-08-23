"""The single doorway for model calls (SPEC §6).

Nothing else in the project may import `anthropic` or shell out to `claude`. That is
not style: the two backends differ in real ways — images by path vs base64, native
structured outputs vs prompt-and-validate, batch vs sequential — and letting those
differences leak into call sites is what makes a backend swap a rewrite instead of a
config change.

Every call is logged with `input_tokens`, `output_tokens` and `projected_usd`, on both
backends. The Max subscription has no marginal dollar cost, but the projection is what
answers "would this be affordable in production", and the budget cap is enforced on it
regardless of backend.

Backends are selected by `ROUGHCUT_BACKEND`; `claude_cli` is the development default.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from . import config


class InferenceError(RuntimeError):
    """A call failed, or produced output that could not be validated."""


class BudgetExceeded(InferenceError):
    """The projected spend cap would be breached. Raised *before* the call."""


@dataclass(frozen=True)
class Request:
    prompt: str
    role: str = config.ROLE_ANALYSIS
    images: Sequence[Path] = ()
    schema: dict | None = None
    system: str | None = None
    # Per-call wall clock. None means `config.call_timeout_s()`. Set short for calls
    # whose whole value is being fast — the estimate that draws a progress bar.
    timeout_s: int | None = None
    # Called as the answer is produced rather than after it, with the text so far:
    # `on_partial("thinking" | "text", text_so_far)`. Optional everywhere — a backend
    # that cannot stream ignores it and behaves exactly as before, which is what keeps
    # the scripted test backends working. See ClaudeCliBackend.
    on_partial: Callable[[str, str], None] | None = field(default=None, compare=False,
                                                          repr=False)


@dataclass
class Result:
    content: Any                     # validated object when a schema was given, else text
    input_tokens: int
    output_tokens: int
    backend: str
    model: str
    projected_usd: float
    latency_ms: int
    raw: str = field(repr=False, default="")


# --------------------------------------------------------------------- ledger

_SPENT = 0.0


def spent_usd() -> float:
    return _SPENT


def reset_spend() -> None:
    """Test hook; also useful between runs in a long-lived process."""
    global _SPENT
    _SPENT = 0.0


def _check_budget(estimate: float) -> None:
    cap = config.budget_usd()
    if _SPENT + estimate > cap:
        raise BudgetExceeded(
            f"projected spend {_SPENT + estimate:.4f} would exceed cap {cap:.2f} "
            f"(ROUGHCUT_BUDGET_USD)")


def _log(result: Result, role: str) -> None:
    global _SPENT
    _SPENT += result.projected_usd
    path = config.ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.time(), "role": role, "backend": result.backend,
                "model": result.model, "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "projected_usd": result.projected_usd,
                "latency_ms": result.latency_ms,
            }) + "\n")
    except OSError:
        pass          # a ledger we cannot write must not take down the pipeline


# ------------------------------------------------------------ schema handling

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Pull a JSON value out of model prose.

    Needed because the CLI backend has no native structured output — SPEC §6.1 makes
    the schema a required *outcome*, not a required mechanism. Tries the whole string,
    then fenced blocks, then the outermost braces/brackets.
    """
    for candidate in _candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise InferenceError(f"no JSON found in response: {text[:400]}")


def _candidates(text: str):
    text = text.strip()
    yield text
    for m in _FENCE.finditer(text):
        yield m.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if 0 <= i < j:
            yield text[i:j + 1]


# ------------------------------------------------------------------ backends

def _emit(on_partial: Callable[[str, str], None] | None, kind: str, text: str) -> None:
    """Hand a partial answer to the caller, and never let it break the call.

    The callback belongs to a progress bar. A progress bar that raises must not cost
    a four-minute model call — the work is worth more than the reporting on it.
    """
    if on_partial is None:
        return
    try:
        on_partial(kind, text)
    except Exception:                                          # pragma: no cover
        pass


class Backend(Protocol):
    name: str

    def complete(self, request: Request) -> Result: ...


class ClaudeCliBackend:
    """Claude Code CLI — bills against the Max subscription, no marginal cost.

    Images are passed by path (SPEC §6.1 rule 1): the CLI reads them from disk, so
    base64 never enters the interface.

    Two ways to run it, and the difference is only *when* you hear about the answer:

      * `--output-format json` — one JSON object on exit. The original path, unchanged,
        and still what every call without an `on_partial` uses.
      * `--output-format stream-json --verbose --include-partial-messages` — one JSON
        object per line as the answer is produced, ending with the same `result` object
        the first form returns whole. Verified on this machine (CLI 2.1.2): the flags
        are accepted together, `stream_event` lines carry `thinking_delta` and
        `text_delta`, and the closing `result` carries the identical `usage` block —
        so token accounting and the ledger are unaffected by which path ran.

    Streaming exists for one reason: a `claude -p` call that only returns at the end
    can report no progress at all, and an Ask is three to four minutes long. With the
    deltas, the caller can count what the model has actually written — "12 of ~20 shots
    decided" — which is progress rather than a spinner.
    """

    name = "claude_cli"

    def complete(self, request: Request) -> Result:
        model = config.model_for(request.role)
        prompt = request.prompt
        if request.images:
            listing = "\n".join(str(Path(p).resolve()) for p in request.images)
            prompt = f"{prompt}\n\nImages to read from disk:\n{listing}"
        if request.schema is not None:
            prompt += ("\n\nRespond with JSON only — no prose, no code fence — "
                       "matching this shape:\n" + json.dumps(request.schema, indent=1))

        if request.on_partial is not None:
            return self._stream(request, prompt, model)

        cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model]
        if request.images:
            # Passing an image by path only works if the CLI is allowed to open it.
            # Without this it answers "I need your permission to read the image" —
            # which then fails schema validation twice and costs two calls to learn.
            # Read is the narrowest tool that does the job; nothing here should be
            # able to edit or run anything.
            cmd += ["--allowedTools", "Read"]
        if request.system:
            cmd += ["--append-system-prompt", request.system]

        timeout = request.timeout_s or config.call_timeout_s()
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout)
        except FileNotFoundError as exc:
            # The likeliest failure on a fresh machine: `claude` lives in ~/.local/bin
            # and a non-login shell does not source .bashrc, so a server started the
            # wrong way sees no CLI at all. Say that, rather than raising a bare
            # OSError that reaches the UI as an opaque 500.
            raise InferenceError(
                "claude CLI not found on PATH. It installs to ~/.local/bin, which a "
                "non-login shell does not pick up — start the server from a login "
                "shell (bash -l) or set ROUGHCUT_BACKEND=anthropic_api.") from exc
        except subprocess.TimeoutExpired as exc:
            raise InferenceError(
                f"claude CLI timed out after {timeout}s "
                f"(raise ROUGHCUT_CALL_TIMEOUT_S)") from exc
        latency = int((time.time() - t0) * 1000)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise InferenceError(f"claude CLI failed: {proc.stderr.strip()[:300]}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise InferenceError(
                f"claude CLI returned non-JSON: {proc.stdout[:300]}") from exc
        return self._result(payload, model, latency)

    # ------------------------------------------------------------- streaming

    # How often `on_partial` may fire. The CLI emits a delta every few hundred
    # characters; a UI that repaints on every one of them would be doing nothing else.
    PARTIAL_TICK_S = 0.4

    def _stream(self, request: Request, prompt: str, model: str) -> Result:
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--model", model]
        if request.images:
            cmd += ["--allowedTools", "Read"]
        if request.system:
            cmd += ["--append-system-prompt", request.system]

        timeout = request.timeout_s or config.call_timeout_s()
        t0 = time.time()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, bufsize=1)
        except FileNotFoundError as exc:
            raise InferenceError(
                "claude CLI not found on PATH. It installs to ~/.local/bin, which a "
                "non-login shell does not pick up — start the server from a login "
                "shell (bash -l) or set ROUGHCUT_BACKEND=anthropic_api.") from exc

        # A watchdog rather than a read deadline: if the model stalls, no lines arrive
        # and a per-line check would never run. Killing the child ends the read loop.
        done = threading.Event()
        killed = threading.Event()
        errors: list[str] = []

        def watchdog() -> None:
            if not done.wait(timeout):
                killed.set()
                proc.kill()

        def drain_stderr() -> None:
            # On its own thread: a full stderr pipe would block the child mid-answer
            # while this side is busy reading stdout, which is a deadlock that only
            # shows up on the one call that happens to be chatty.
            if proc.stderr is not None:
                errors.append(proc.stderr.read() or "")

        threading.Thread(target=watchdog, daemon=True).start()
        err_thread = threading.Thread(target=drain_stderr, daemon=True)
        err_thread.start()

        payload: dict | None = None
        thinking, text = [], []
        last_tick = 0.0
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue          # the CLI prints the odd non-JSON line; skip it
                kind = event.get("type")
                if kind == "result":
                    payload = event
                    continue
                if kind != "stream_event":
                    continue
                delta = (event.get("event") or {}).get("delta") or {}
                if delta.get("type") == "thinking_delta":
                    thinking.append(delta.get("thinking") or "")
                elif delta.get("type") == "text_delta":
                    text.append(delta.get("text") or "")
                else:
                    continue
                now = time.time()
                if now - last_tick >= self.PARTIAL_TICK_S:
                    last_tick = now
                    _emit(request.on_partial,
                          "text" if text else "thinking",
                          "".join(text) if text else "".join(thinking))
            proc.wait()
        finally:
            done.set()
            err_thread.join(timeout=5)
            stderr = "".join(errors)
        # One last flush, so the caller's final count is of the whole answer and not of
        # whatever the throttle happened to let through.
        _emit(request.on_partial, "text" if text else "thinking",
              "".join(text) if text else "".join(thinking))

        latency = int((time.time() - t0) * 1000)
        if killed.is_set():
            raise InferenceError(
                f"claude CLI timed out after {timeout}s "
                f"(raise ROUGHCUT_CALL_TIMEOUT_S)")
        if payload is None:
            raise InferenceError(
                "claude CLI streamed no result event: "
                f"{(stderr.strip() or ''.join(text))[:300]}")
        return self._result(payload, model, latency)

    # --------------------------------------------------------------- shared

    def _result(self, payload: dict, model: str, latency: int) -> Result:
        """The `result` object, whichever way it arrived. Both output formats end with
        the same one, so token accounting cannot differ between the two paths."""
        text = payload.get("result", "")
        if payload.get("is_error"):
            # The login prompt arrives this way, and is the most likely failure on a
            # fresh machine — surface it as itself rather than as a parse error.
            raise InferenceError(f"claude CLI error: {text[:200]}")

        usage = payload.get("usage", {}) or {}
        # Cached tokens count. The CLI splits input across `input_tokens`,
        # `cache_creation_input_tokens` and `cache_read_input_tokens`, and on a prompt
        # this size most of it lands in the cache fields — reading only the first
        # would report a near-zero projection for a call that really did process tens
        # of thousands of tokens. Undercounting is the dangerous direction: the whole
        # point of `projected_usd` is to answer "is this affordable in production"
        # while developing on a subscription where nothing is charged (SPEC §7).
        # Cache reads are billed below full input rate in reality, so this is a
        # deliberate over-estimate rather than a precise bill.
        in_tok = sum(int(usage.get(k, 0) or 0) for k in
                     ("input_tokens", "cache_creation_input_tokens",
                      "cache_read_input_tokens"))
        out_tok = int(usage.get("output_tokens", 0) or 0)
        return Result(
            content=text, input_tokens=in_tok, output_tokens=out_tok,
            backend=self.name, model=model,
            projected_usd=config.projected_usd(model, in_tok, out_tok),
            latency_ms=latency, raw=text,
        )


class AnthropicApiBackend:
    """Production path. `anthropic` is imported here and nowhere else in the project."""

    name = "anthropic_api"

    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError as exc:                       # pragma: no cover
            raise InferenceError(
                "anthropic package not installed; use ROUGHCUT_BACKEND=claude_cli"
            ) from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise InferenceError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic()

    def complete(self, request: Request) -> Result:
        import base64
        import mimetypes

        model = config.model_for(request.role)
        blocks: list[dict] = []
        for p in request.images:
            path = Path(p)
            blocks.append({"type": "image", "source": {
                "type": "base64",
                "media_type": mimetypes.guess_type(path.name)[0] or "image/jpeg",
                "data": base64.b64encode(path.read_bytes()).decode(),
            }})
        prompt = request.prompt
        if request.schema is not None:
            prompt += ("\n\nRespond with JSON only matching this shape:\n"
                       + json.dumps(request.schema, indent=1))
        blocks.append({"type": "text", "text": prompt})

        kwargs: dict = {"model": model, "max_tokens": 8192,
                        "messages": [{"role": "user", "content": blocks}]}
        if request.system:
            kwargs["system"] = request.system

        t0 = time.time()
        msg = self._client.messages.create(**kwargs)
        latency = int((time.time() - t0) * 1000)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        in_tok, out_tok = msg.usage.input_tokens, msg.usage.output_tokens
        return Result(
            content=text, input_tokens=in_tok, output_tokens=out_tok,
            backend=self.name, model=model,
            projected_usd=config.projected_usd(model, in_tok, out_tok),
            latency_ms=latency, raw=text,
        )


_OVERRIDE: Backend | None = None


def set_backend(backend: Backend | None) -> None:
    """Install a backend explicitly. Tests use this; nothing else should."""
    global _OVERRIDE
    _OVERRIDE = backend


def get_backend() -> Backend:
    if _OVERRIDE is not None:
        return _OVERRIDE
    name = config.backend_name()
    if name == "claude_cli":
        return ClaudeCliBackend()
    if name == "anthropic_api":
        return AnthropicApiBackend()
    raise InferenceError(f"unknown ROUGHCUT_BACKEND {name!r}")


# -------------------------------------------------------------------- public

def complete(prompt: str, *, role: str = config.ROLE_ANALYSIS,
             images: Sequence[Path] = (), schema: dict | None = None,
             system: str | None = None,
             validate: Callable[[Any], Any] | None = None,
             retries: int = 1, timeout_s: int | None = None,
             on_partial: Callable[[str, str], None] | None = None) -> Result:
    """One call. Returns validated content when a schema is given, or raises.

    `retries` is a *bounded* re-ask on validation failure, which is how the CLI
    backend reaches schema fidelity the API backend gets natively. Bounded because an
    unbounded retry loop against a model that has misunderstood the schema is just a
    slower way to fail, and it spends budget doing it.

    `on_partial(kind, text_so_far)` turns the call from something you wait for into
    something you can watch: a backend that supports it reports the answer as it is
    written, and one that does not simply never calls it. `timeout_s` overrides the
    per-call wall clock for calls whose whole value is being quick.
    """
    backend = get_backend()
    request = Request(prompt=prompt, role=role, images=tuple(images),
                      schema=schema, system=system, timeout_s=timeout_s,
                      on_partial=on_partial)

    last: Exception | None = None
    for attempt in range(retries + 1):
        _check_budget(0.05)          # coarse pre-flight; real cost logged after
        result = backend.complete(request)
        _log(result, role)
        if schema is None:
            return result
        try:
            parsed = extract_json(result.raw or str(result.content))
            result.content = validate(parsed) if validate else parsed
            return result
        except (InferenceError, ValueError) as exc:
            last = exc
            request = Request(
                prompt=(request.prompt + "\n\nYour previous reply could not be parsed"
                        f" as the required JSON ({exc}). Reply with JSON only."),
                role=role, images=tuple(images), schema=schema, system=system,
                timeout_s=timeout_s, on_partial=on_partial)
    raise InferenceError(f"schema validation failed after {retries + 1} attempts: {last}")


def complete_many(requests: Sequence[Request]) -> list[Result]:
    """The only batching surface (SPEC §6.1). The CLI backend loops; an API backend
    may fan out to the Batch API internally."""
    backend = get_backend()
    out: list[Result] = []
    for req in requests:
        _check_budget(0.05)
        result = backend.complete(req)
        _log(result, req.role)
        out.append(result)
    return out
