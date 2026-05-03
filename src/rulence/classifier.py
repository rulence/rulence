from __future__ import annotations

import json
import os
import re
from urllib import request

from .models import Classification


TIER_LABELS = {
    0: "direct",
    1: "trivial",
    2: "mild",
    3: "moderate",
    4: "complex",
    5: "high-risk",
}

HIGH_RISK_TERMS = {
    "password",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "api key",
    "api_key",
    "token",
    "ssn",
    "passport",
    "bank",
    "payment",
    "credit card",
    "medical",
    "legal",
    "lawsuit",
    "tax",
    "production",
    "prod",
}

SENSITIVE_TERMS = HIGH_RISK_TERMS - {"production", "prod"}

DESTRUCTIVE_TERMS = {
    "delete",
    "drop",
    "destroy",
    "erase",
    "wipe",
    "reset",
    "remove",
    "cancel",
    "terminate",
    "revoke",
    "force-push",
    "reset --hard",
}

EXTERNAL_ACTION_TERMS = {
    "send",
    "email",
    "post",
    "publish",
    "submit",
    "upload",
    "invite",
    "message",
    "tweet",
    "comment",
}

TOOL_TERMS = {
    "run",
    "execute",
    "install",
    "deploy",
    "github",
    "browser",
    "terminal",
    "shell",
    "database",
    "repo",
    "git",
    "branch",
    "file",
    "api",
    "mcp",
    "db",
    "force-push",
}

COMPLEX_TERMS = {
    "debug",
    "architecture",
    "migration",
    "migrate",
    "force-push",
    "rebase",
    "refactor",
    "research",
    "investigate",
    "root cause",
    "tradeoff",
    "formal",
    "verify",
    "prove",
    "constraint",
    "multi-step",
    "plan",
    "synthesis",
}

FORMAL_REASONING_TERMS = {
    "prove",
    "proof",
    "formal",
    "verify",
    "constraint",
    "solver",
    "theorem",
    "logic",
    "consistency",
}

AMBIGUITY_TERMS = {
    "thing",
    "stuff",
    "whatever",
    "somehow",
    "maybe",
    "do it",
    "fix this",
    "make it work",
}


def classify_task(task: str) -> Classification:
    text = task.strip()
    normalized = re.sub(r"\s+", " ", text.lower())
    words = re.findall(r"[a-zA-Z0-9_'-]+", normalized)

    signals = {
        "word_count": len(words),
        "high_risk_terms": _matches(normalized, HIGH_RISK_TERMS),
        "destructive_terms": _matches(normalized, DESTRUCTIVE_TERMS),
        "external_action_terms": _matches(normalized, EXTERNAL_ACTION_TERMS),
        "tool_terms": _matches(normalized, TOOL_TERMS),
        "complex_terms": _matches(normalized, COMPLEX_TERMS),
        "formal_reasoning_terms": _matches(normalized, FORMAL_REASONING_TERMS),
        "ambiguity_terms": _matches(normalized, AMBIGUITY_TERMS),
        "multi_clause": _has_multi_clause(normalized),
    }

    reasons: list[str] = []
    risk_score = (
        len(signals["high_risk_terms"]) * 3
        + len(signals["destructive_terms"]) * 2
        + len(signals["external_action_terms"])
    )
    complexity_score = (
        len(signals["complex_terms"]) * 2
        + len(signals["formal_reasoning_terms"]) * 2
        + len(signals["tool_terms"])
        + len(signals["ambiguity_terms"])
        + (2 if signals["multi_clause"] else 0)
        + (1 if signals["word_count"] > 18 else 0)
        + (2 if signals["word_count"] > 38 else 0)
    )

    if signals["high_risk_terms"]:
        reasons.append("high-impact domain or sensitive term detected")
    if signals["destructive_terms"]:
        reasons.append("destructive or irreversible action term detected")
    if signals["external_action_terms"]:
        reasons.append("task may communicate or transmit data externally")
    if signals["complex_terms"]:
        reasons.append("complex reasoning/workflow term detected")
    if signals["formal_reasoning_terms"]:
        reasons.append("formal reasoning or proof term detected")
    if signals["tool_terms"]:
        reasons.append("task likely requires tools or local system access")
    if signals["multi_clause"]:
        reasons.append("task contains multiple dependent clauses")
    if signals["ambiguity_terms"]:
        reasons.append("task contains ambiguous wording")

    if set(signals["high_risk_terms"]) & SENSITIVE_TERMS:
        tier = 5
    elif risk_score >= 4 or (signals["destructive_terms"] and signals["high_risk_terms"]):
        tier = 5
    elif complexity_score >= 7 or (signals["tool_terms"] and signals["complex_terms"]):
        tier = 4
    elif complexity_score >= 4 or signals["formal_reasoning_terms"]:
        tier = 3
    elif complexity_score >= 2 or signals["tool_terms"]:
        tier = 2
    elif len(words) <= 5 and not risk_score:
        tier = 0
        reasons.append("short direct task with no risk/tool signals")
    else:
        tier = 1
        reasons.append("simple task with low risk/tool signals")

    signals["risk_score"] = risk_score
    signals["complexity_score"] = complexity_score
    local_model = _local_model_tier(text, signals)
    if local_model:
        model_tier, reason = local_model
        signals["local_model_tier"] = model_tier
        tier = model_tier
        reasons.append(f"local model fallback: {reason}")

    return Classification(
        task=text,
        tier=tier,
        label=f"Tier {tier} / {TIER_LABELS[tier]}",
        reasons=tuple(dict.fromkeys(reasons)),
        signals=signals,
    )


def _matches(text: str, terms: set[str]) -> list[str]:
    found: list[str] = []
    for term in terms:
        if term == "do it" and text.strip() != "do it":
            continue
        pattern = _term_pattern(term)
        if re.search(pattern, text):
            found.append(term)
    return sorted(found)


def _term_pattern(term: str) -> str:
    escaped = re.escape(term)
    escaped = escaped.replace(r"\ ", r"\s+")
    return rf"(?<![a-zA-Z0-9_]){escaped}(?![a-zA-Z0-9_])"


def _has_multi_clause(text: str) -> bool:
    clause_markers = (" and ", " then ", " after ", " before ", " while ", " but ")
    return sum(marker in text for marker in clause_markers) >= 2


def _local_model_tier(task: str, signals: dict) -> tuple[int, str] | None:
    model = os.environ.get("RULENCE_CLASSIFIER_MODEL")
    should_try = signals.get("ambiguity_terms") or (
        signals.get("complexity_score") == 0
        and signals.get("risk_score") == 0
        and int(signals.get("word_count", 0)) > 8
    )
    if not model or not should_try:
        return None

    endpoint = os.environ.get("RULENCE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    timeout = float(os.environ.get("RULENCE_CLASSIFIER_TIMEOUT", "1.5"))
    prompt = (
        "Classify this agent task into a Rulence tier from 0 to 5.\n"
        "0=direct, 1=trivial, 2=mild, 3=moderate, 4=complex, 5=high-risk.\n"
        "Return JSON only: {\"tier\": number, \"reason\": \"short reason\"}.\n\n"
        f"Task: {task}"
    )
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
    req = request.Request(f"{endpoint}/api/generate", data=payload, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            outer = json.loads(response.read().decode("utf-8"))
        inner = json.loads(str(outer.get("response", "{}")))
        tier = int(inner["tier"])
        if tier not in TIER_LABELS:
            return None
        return tier, str(inner.get("reason", "ambiguous task classified locally"))
    except Exception:
        signals["local_model_fallback"] = "unavailable"
        return None
