"""Optional LLM polish for explanations. Disabled unless LLM_PROVIDER + key are configured via env.

The deterministic, factor-based narrative is always produced first; the LLM may only rephrase it.
"""
from __future__ import annotations

from app.config import get_settings


def available() -> bool:
    s = get_settings()
    if s.llm_provider == "anthropic":
        return bool(s.anthropic_api_key)
    if s.llm_provider == "openai":
        return bool(s.openai_api_key)
    return False


def polish(text: str) -> tuple[str, str | None]:
    """Returns (text, provider_used). Falls back to the input text on any failure."""
    if not available():
        return text, None
    s = get_settings()
    prompt = ("Rewrite the following banking analyst note in clear, concise plain English. Do not add, remove or change any figure, "
              "name, rule or decision, and keep the 'demo / synthetic data' caveats.\n\n" + text)
    try:
        import httpx

        if s.llm_provider == "anthropic":
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=20,
                           headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
                           json={"model": s.llm_model or "claude-haiku-4-5", "max_tokens": 600, "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            return r.json()["content"][0]["text"].strip(), "anthropic"
        r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=20, headers={"Authorization": f"Bearer {s.openai_api_key}"},
                       json={"model": s.llm_model or "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip(), "openai"
    except Exception:  # noqa: BLE001
        return text, None
