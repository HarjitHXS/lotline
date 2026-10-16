"""The ONE place that talks to an AI model.

Pick a provider with LLM_PROVIDER in .env (anthropic | gemini | ollama).
If it's not set, we use whichever key exists, else local Ollama.
Swapping models later = change .env, nothing else.
"""
import json
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()


def provider() -> str:
    p = os.getenv("LLM_PROVIDER")
    if p:
        return p
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return "ollama"


def model_name() -> str:
    return {
        "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        "gemini": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "ollama": os.getenv("OLLAMA_MODEL", "qwen3:8b"),
    }[provider()]


def _parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in ``` fences or add a sentence. Grab the {...}."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError(f"no JSON in model output: {text[:200]}")
    return json.loads(m.group(0))


def complete_json(system: str, user: str, max_tokens: int = 800) -> dict:
    p = provider()
    if p == "anthropic":
        import anthropic

        msg = anthropic.Anthropic().messages.create(
            model=model_name(),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return _parse_json(msg.content[0].text)

    if p == "gemini":
        from google import genai

        resp = genai.Client().models.generate_content(
            model=model_name(),
            contents=user,
            config={"system_instruction": system, "response_mime_type": "application/json",
                    "max_output_tokens": max_tokens},
        )
        return _parse_json(resp.text)

    # Local Ollama (your llm-learning models). format=json forces valid JSON.
    resp = httpx.post(
        os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
        json={
            "model": model_name(),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": "json",
            "stream": False,
            "think": False,
            "options": {"num_ctx": 16384, "temperature": 0},
        },
        timeout=300,
    )
    resp.raise_for_status()
    return _parse_json(resp.json()["message"]["content"])


def chat(system: str, messages: list[dict], max_tokens: int = 700) -> str:
    """Plain-text multi-turn chat. messages = [{"role": "user"|"assistant", "content": str}, ...]"""
    p = provider()
    if p == "anthropic":
        import anthropic

        msg = anthropic.Anthropic().messages.create(model=model_name(), max_tokens=max_tokens, system=system,
                                                    messages=messages)
        return msg.content[0].text.strip()

    if p == "gemini":
        from google import genai

        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in messages]
        resp = genai.Client().models.generate_content(
            model=model_name(), contents=contents,
            config={"system_instruction": system, "max_output_tokens": max_tokens})
        return (resp.text or "").strip()

    resp = httpx.post(
        os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
        json={"model": model_name(), "messages": [{"role": "system", "content": system}, *messages],
              "stream": False, "think": False, "options": {"num_ctx": 16384, "temperature": 0.2, "num_predict": max_tokens}},
        timeout=300,
    )
    resp.raise_for_status()
    return re.sub(r"<think>.*?</think>", "", resp.json()["message"]["content"], flags=re.S).strip()


if __name__ == "__main__":
    print(provider(), model_name())
    print(complete_json("Reply in JSON.", 'Return {"status": "ready"}'))
