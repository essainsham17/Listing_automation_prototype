"""OpenAI-style client shim that routes chat completion calls to the local Claude Code CLI."""

from __future__ import annotations

import base64
import json
import logging
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a data matching service for a car dealership's listing system. "
    "You reply with a single valid JSON object and nothing else — no "
    "explanation, no commentary, no code fences. When asked to choose from a "
    "fixed list of options you must return one of those options exactly as "
    "written, or null. Never invent a value."
)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _json_text_from_reply(text: str) -> str:
    """Strips code fences and surrounding prose from a reply, returning the parseable JSON substring if any."""
    if not text:
        return text
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            candidate = cleaned[start:end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
    return cleaned


class _Message:
    """Minimal OpenAI-style message holding reply content and an empty refusal."""
    def __init__(self, content: str | None):
        """Stores the content and sets refusal to None."""
        self.content = content
        self.refusal = None


class _Choice:
    """Minimal OpenAI-style choice holding a message and a finish reason."""
    def __init__(self, content: str | None, finish_reason: str):
        """Wraps the content in a _Message and stores the finish reason."""
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Usage:
    """Minimal OpenAI-style token usage record with prompt, completion and total counts."""
    def __init__(self, prompt_tokens: int, completion_tokens: int):
        """Stores prompt and completion token counts and computes their total."""
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _Response:
    """Minimal OpenAI-style completion response with one choice, usage and no error."""
    def __init__(self, content: str | None, finish_reason: str, usage: _Usage | None):
        """Builds a single-choice list from the content and finish reason and stores usage."""
        self.choices = [_Choice(content, finish_reason)]
        self.usage = usage
        self.error = None


def _split_message(messages):
    """Splits chat messages into one joined prompt string and a list of image data URIs."""
    texts, images = [], []
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str):
            if content.strip():
                texts.append(content)
            continue
        for block in content or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                texts.append(block["text"])
            elif block.get("type") == "image_url":
                url = (block.get("image_url") or {}).get("url", "")
                if url:
                    images.append(url)
    return "\n\n".join(texts), images


def _write_images(data_uris, tmpdir):
    """Decodes base64 image data URIs into numbered JPEG files in the given folder and returns their paths."""
    paths = []
    for i, uri in enumerate(data_uris):
        b64 = uri.split(",", 1)[1] if "," in uri else uri
        p = Path(tmpdir) / f"page-{i + 1:03d}.jpg"
        p.write_bytes(base64.b64decode(b64))
        paths.append(p)
    return paths


class _Completions:
    """Stand-in for chat.completions that answers create calls through the Claude CLI."""
    def __init__(self, timeout: float, model: str | None):
        """Stores the CLI timeout and model to use for each call."""
        self._timeout = timeout
        self._model = model

    def create(self, **kwargs):
        """Sends the messages and any images to the CLI and returns an OpenAI-shaped response with cleaned JSON."""
        from app.claude_cli import ClaudeCLI, ClaudeCLIError

        prompt, image_uris = _split_message(kwargs.get("messages"))
        if not prompt:
            return _Response(None, "empty_prompt", None)

        with tempfile.TemporaryDirectory(prefix="cli-pages-") as tmp:
            if image_uris:
                paths = _write_images(image_uris, tmp)
                listed = "\n".join(f"{i + 1}: {p}" for i, p in enumerate(paths))
                prompt = (
                    f"{prompt}\n\nRead each of these image files, in this order, "
                    f"and answer from what they contain:\n{listed}"
                )

            agent = ClaudeCLI(
                system_prompt=SYSTEM_PROMPT,
                model=self._model,
                allowed_tools=["Read"] if image_uris else None,
                timeout=self._timeout,
            )
            try:
                reply = agent.ask(prompt)
            except ClaudeCLIError as e:
                raise RuntimeError(f"Claude CLI failed: {e}") from e

        usage = agent.usage or {}
        u = None
        if "input_tokens" in usage or "output_tokens" in usage:
            u = _Usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        if agent.denied_tools:
            logger.warning("claude_cli_client: tool calls were denied: %s", agent.denied_tools)

        return _Response(_json_text_from_reply(reply), "stop", u)


class _Chat:
    """Stand-in for client.chat exposing a completions attribute."""
    def __init__(self, timeout: float, model: str | None):
        """Creates the completions stand-in with the given timeout and model."""
        self.completions = _Completions(timeout, model)


class ClaudeCLIClient:
    """Drop-in substitute for openai.OpenAI that sends chat completions to the Claude CLI."""

    def __init__(self, timeout: float = 900.0, model: str | None = None):
        """Creates the chat attribute with the given timeout and model."""
        self.chat = _Chat(timeout, model)
