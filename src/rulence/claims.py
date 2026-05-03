"""Rulence claims ledger validator and public-copy scanner.

The claims ledger lives at ``docs/claims.yml`` and tells the codebase exactly
what Rulence is allowed to say in user-facing copy. The verifier:

* loads and validates ``docs/claims.yml`` against a fixed schema;
* enforces that ``unsupported`` and unqualified ``aspirational`` claims are
  not advertised;
* scans README, docs, CLI help, and packaging metadata for blocked phrases.

This module is deliberately self-contained: it ships a tiny YAML subset
parser so the verifier has no new third-party dependency.

The scanner is advisory at runtime — ``rulence claims verify`` returns a
non-zero exit code when violations are found, but it does not block any
agent activity. M0 is documentation, claim inventory, and CI-friendly
verification only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

REQUIRED_FIELDS: tuple[str, ...] = (
    "claim_id",
    "claim_text",
    "category",
    "status",
    "current_evidence",
    "required_components",
    "acceptance_tests",
    "public_copy_allowed",
    "allowed_phrasings",
    "blocked_phrases",
    "notes",
)

VALID_STATUSES: tuple[str, ...] = (
    "supported",
    "partial",
    "advisory",
    "aspirational",
    "unsupported",
)

LIST_FIELDS: tuple[str, ...] = (
    "current_evidence",
    "required_components",
    "acceptance_tests",
    "allowed_phrasings",
    "blocked_phrases",
)

# Claim categories that require a passing eval in the matching eval
# category before the claim verifier will sign off on it. The verifier
# only runs this check when explicitly asked (``require_evals=True``)
# so unrelated CI runs do not need the eval suite to pass.
CLAIM_CATEGORY_TO_EVAL_CATEGORY: dict[str, str] = {
    "memory": "memory",
    "context": "context",
    "audit": "token",
    "cross_runtime": "cross_agent",
}

# Per-claim overrides where the category mapping is too coarse. When a
# claim id appears here, the verifier requires at least one passing
# eval in the listed eval category, regardless of the claim's own
# category. Unlisted claims fall back to ``CLAIM_CATEGORY_TO_EVAL_CATEGORY``.
CLAIM_ID_TO_REQUIRED_EVAL_CATEGORY: dict[str, str] = {
    "context_compression_local_benchmark": "context",
    "context_assembly": "context",
    "memory_arbitration_policy": "memory",
    "shared_agent_context": "cross_agent",
    "token_accounting": "token",
}

# A blocked phrase is allowed when the surrounding markdown section's nearest
# preceding heading line contains one of these qualifiers.
ROADMAP_QUALIFIERS: tuple[str, ...] = (
    "roadmap",
    "future",
    "aspirational",
    "planned",
    "not yet",
    "deferred",
    "do not use",
    "avoid",
    "unsupported",
    "blocked phrase",
    "won't ship",
    # Sections that explicitly enumerate gaps are also exempt: a heading like
    # "What Rulence does not do" or "Honest limitations" is admitting absence,
    # not asserting capability.
    "does not",
    "limitations",
)


@dataclass(frozen=True)
class Claim:
    raw: dict[str, Any]

    @property
    def claim_id(self) -> str:
        value = self.raw.get("claim_id")
        return str(value) if value is not None else ""

    @property
    def claim_text(self) -> str:
        value = self.raw.get("claim_text")
        return str(value) if value is not None else ""

    @property
    def status(self) -> str:
        value = self.raw.get("status")
        return str(value) if value is not None else ""

    @property
    def category(self) -> str:
        value = self.raw.get("category")
        return str(value) if value is not None else ""

    @property
    def public_copy_allowed(self) -> Any:
        return self.raw.get("public_copy_allowed")

    @property
    def blocked_phrases(self) -> list[str]:
        return [str(p) for p in (self.raw.get("blocked_phrases") or []) if str(p)]

    @property
    def allowed_phrasings(self) -> list[str]:
        return [str(p) for p in (self.raw.get("allowed_phrasings") or []) if str(p)]

    @property
    def current_evidence(self) -> list[str]:
        return [str(p) for p in (self.raw.get("current_evidence") or []) if str(p)]

    @property
    def eval_evidence(self) -> list[str]:
        """Eval ids the claim relies on, drawn from an optional ``eval_evidence`` list."""
        return [str(p) for p in (self.raw.get("eval_evidence") or []) if str(p)]


@dataclass(frozen=True)
class ClaimsLedger:
    version: int
    claims: tuple[Claim, ...]
    path: Path | None = None


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    message: str = ""
    claim_id: str = ""
    phrase: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "claim_id": self.claim_id,
            "phrase": self.phrase,
        }


def load_claims(path: str | Path) -> ClaimsLedger:
    """Parse ``docs/claims.yml`` (or another claims file) into a ledger."""
    resolved = Path(path).expanduser()
    text = resolved.read_text(encoding="utf-8")
    data = parse_yaml(text)
    if not isinstance(data, dict):
        raise ValueError(f"{resolved}: top level must be a mapping")
    raw_claims = data.get("claims")
    if not isinstance(raw_claims, list):
        raise ValueError(f"{resolved}: 'claims' must be a list")
    claims: list[Claim] = []
    for index, entry in enumerate(raw_claims):
        if not isinstance(entry, dict):
            raise ValueError(f"{resolved}: claim #{index} must be a mapping")
        claims.append(Claim(raw=entry))
    version_value = data.get("version", 1)
    try:
        version = int(version_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{resolved}: 'version' must be an integer") from exc
    return ClaimsLedger(version=version, claims=tuple(claims), path=resolved)


def validate_ledger(ledger: ClaimsLedger) -> list[Violation]:
    """Validate the ledger's structural rules."""
    violations: list[Violation] = []
    seen_ids: set[str] = set()
    file_label = str(ledger.path) if ledger.path else "<ledger>"

    for index, claim in enumerate(ledger.claims):
        location = f"claim #{index}" + (f" ({claim.claim_id})" if claim.claim_id else "")

        for field_name in REQUIRED_FIELDS:
            if field_name not in claim.raw:
                violations.append(
                    Violation(
                        file=file_label,
                        line=0,
                        claim_id=claim.claim_id,
                        message=f"{location}: missing required field '{field_name}'",
                    )
                )

        for field_name in LIST_FIELDS:
            value = claim.raw.get(field_name)
            if value is None:
                continue
            if not isinstance(value, list):
                violations.append(
                    Violation(
                        file=file_label,
                        line=0,
                        claim_id=claim.claim_id,
                        message=f"{location}: field '{field_name}' must be a list",
                    )
                )
                continue
            for item in value:
                if not isinstance(item, str):
                    violations.append(
                        Violation(
                            file=file_label,
                            line=0,
                            claim_id=claim.claim_id,
                            message=f"{location}: '{field_name}' entries must be strings",
                        )
                    )

        if claim.claim_id:
            if claim.claim_id in seen_ids:
                violations.append(
                    Violation(
                        file=file_label,
                        line=0,
                        claim_id=claim.claim_id,
                        message=f"{location}: duplicate claim_id '{claim.claim_id}'",
                    )
                )
            seen_ids.add(claim.claim_id)

        status = claim.status
        if status and status not in VALID_STATUSES:
            violations.append(
                Violation(
                    file=file_label,
                    line=0,
                    claim_id=claim.claim_id,
                    message=(
                        f"{location}: invalid status '{status}'. "
                        f"Must be one of: {', '.join(VALID_STATUSES)}"
                    ),
                )
            )

        public_copy_allowed = claim.public_copy_allowed
        if not isinstance(public_copy_allowed, bool):
            violations.append(
                Violation(
                    file=file_label,
                    line=0,
                    claim_id=claim.claim_id,
                    message=f"{location}: 'public_copy_allowed' must be a boolean",
                )
            )
        else:
            if status == "unsupported" and public_copy_allowed:
                violations.append(
                    Violation(
                        file=file_label,
                        line=0,
                        claim_id=claim.claim_id,
                        message=(
                            f"{location}: status is 'unsupported' but public_copy_allowed is true"
                        ),
                    )
                )
            if status == "aspirational" and public_copy_allowed:
                if not _has_roadmap_qualifier(claim.claim_text):
                    violations.append(
                        Violation(
                            file=file_label,
                            line=0,
                            claim_id=claim.claim_id,
                            message=(
                                f"{location}: aspirational claim is public but the "
                                "claim_text has no roadmap/future/planned qualifier"
                            ),
                        )
                    )

        if status == "supported" and not claim.current_evidence:
            violations.append(
                Violation(
                    file=file_label,
                    line=0,
                    claim_id=claim.claim_id,
                    message=f"{location}: supported claim must cite current_evidence",
                )
            )

        if status == "advisory" and claim.allowed_phrasings:
            joined = " ".join(claim.allowed_phrasings).lower()
            if not any(token in joined for token in ("advisory", "optional", "requires host")):
                violations.append(
                    Violation(
                        file=file_label,
                        line=0,
                        claim_id=claim.claim_id,
                        message=(
                            f"{location}: advisory claim's allowed_phrasings should include "
                            "'advisory', 'optional', or 'requires host'"
                        ),
                    )
                )

    return violations


def scan_targets(root: Path) -> Iterator[Path]:
    """Yield user-facing files to scan, in the order the spec lists them."""
    yield root / "README.md"
    docs = root / "docs"
    if docs.exists():
        for entry in sorted(docs.rglob("*.md")):
            yield entry
    yield root / "src" / "rulence" / "cli.py"
    yield root / "src" / "rulence" / "installers.py"
    yield root / "package.json"
    yield root / "pyproject.toml"


def scan_file(path: Path, ledger: ClaimsLedger, root: Path | None = None) -> list[Violation]:
    """Scan one file for blocked phrases drawn from the ledger."""
    if not path.exists() or not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    rel_path = _relative_label(path, root)
    is_markdown = path.suffix.lower() == ".md"

    seen: set[tuple[str, int, str]] = set()
    violations: list[Violation] = []

    for claim in ledger.claims:
        for phrase in claim.blocked_phrases:
            for offset in _find_all_substrings(text, phrase):
                if is_markdown and _is_in_roadmap_section(text, offset):
                    continue
                line = text.count("\n", 0, offset) + 1
                key = (rel_path, line, phrase.lower())
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    Violation(
                        file=rel_path,
                        line=line,
                        phrase=phrase,
                        claim_id=claim.claim_id,
                        message=(
                            f"blocked phrase '{phrase}' (claim: {claim.claim_id}); "
                            "remove or move under a roadmap/aspirational section"
                        ),
                    )
                )
    return violations


def verify_repo(
    root: str | Path,
    *,
    require_evals: bool = False,
    eval_report: Any = None,
) -> list[Violation]:
    """Run the full verification: schema + scan, plus optional eval gate.

    When ``require_evals`` is True, the verifier additionally runs
    :func:`validate_with_evals` against the supplied or freshly
    generated :class:`rulence.evals.runner.EvalReport`. This is opt-in
    so day-to-day ``rulence claims verify`` does not require the eval
    suite to pass.
    """
    repo = Path(root).expanduser().resolve()
    ledger_path = repo / "docs" / "claims.yml"
    if not ledger_path.exists():
        return [
            Violation(
                file=str(ledger_path),
                line=0,
                message="docs/claims.yml is missing; create it before claiming features",
            )
        ]
    ledger = load_claims(ledger_path)
    violations: list[Violation] = list(validate_ledger(ledger))
    for target in scan_targets(repo):
        violations.extend(scan_file(target, ledger, repo))
    if require_evals:
        report = eval_report
        if report is None:
            try:
                from .evals import EvalRunner

                report = EvalRunner().run()
            except Exception as exc:
                violations.append(
                    Violation(
                        file=str(ledger_path),
                        line=0,
                        message=f"could not run evals: {exc}",
                    )
                )
                report = None
        if report is not None:
            violations.extend(validate_with_evals(ledger, report))
    return violations


def validate_with_evals(ledger: ClaimsLedger, report: Any) -> list[Violation]:
    """Require supported claims to have at least one passing eval in the
    matching eval category.

    ``report`` is duck-typed: any object exposing a ``results`` iterable
    of items with ``status``, ``category``, and ``claim_ids`` attributes
    (or string keys, when the report has been deserialized) works.
    """
    violations: list[Violation] = []
    file_label = str(ledger.path) if ledger.path else "<ledger>"
    passing = _index_passing_results(report)
    for claim in ledger.claims:
        if claim.status != "supported":
            continue
        required_eval_category = _required_eval_category(claim)
        if required_eval_category is None:
            continue
        eval_passing_for_claim = passing.get((claim.claim_id, required_eval_category), 0)
        category_passing = passing.get(("__any__", required_eval_category), 0)
        explicit_evidence_ok = False
        if claim.eval_evidence:
            for eval_id in claim.eval_evidence:
                if passing.get(("__id__", eval_id), 0) > 0:
                    explicit_evidence_ok = True
                    break
        if eval_passing_for_claim == 0 and category_passing == 0 and not explicit_evidence_ok:
            violations.append(
                Violation(
                    file=file_label,
                    line=0,
                    claim_id=claim.claim_id,
                    message=(
                        f"claim '{claim.claim_id}' (status=supported) needs at "
                        f"least one passing eval in the '{required_eval_category}' "
                        "category before it can advertise"
                    ),
                )
            )
    return violations


def _required_eval_category(claim: Claim) -> str | None:
    explicit = CLAIM_ID_TO_REQUIRED_EVAL_CATEGORY.get(claim.claim_id)
    if explicit:
        return explicit
    return CLAIM_CATEGORY_TO_EVAL_CATEGORY.get(claim.category)


def _index_passing_results(report: Any) -> dict[tuple[str, str], int]:
    """Build a lookup of ``(claim_id, category) -> count`` of passing evals.

    Two synthetic keys are also produced:
    * ``("__any__", category)`` — number of passing evals in a category
      regardless of which claim they map to.
    * ``("__id__", eval_id)`` — set to 1 when the eval id passed.
    """
    counts: dict[tuple[str, str], int] = {}
    results = getattr(report, "results", None)
    if results is None and isinstance(report, dict):
        results = report.get("results") or ()
    for result in results or ():
        status = _attr_or_key(result, "status")
        if status != "pass":
            continue
        category = str(_attr_or_key(result, "category") or "")
        eval_id = str(_attr_or_key(result, "eval_id") or "")
        claim_ids = _attr_or_key(result, "claim_ids") or ()
        if category:
            counts[("__any__", category)] = counts.get(("__any__", category), 0) + 1
        if eval_id:
            counts[("__id__", eval_id)] = 1
        for claim_id in claim_ids:
            counts[(str(claim_id), category)] = (
                counts.get((str(claim_id), category), 0) + 1
            )
    return counts


def _attr_or_key(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


# ---------------------------------------------------------------------------
# helpers


def _relative_label(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _has_roadmap_qualifier(text: str) -> bool:
    lowered = text.lower()
    return any(qualifier in lowered for qualifier in ROADMAP_QUALIFIERS)


def _is_in_roadmap_section(text: str, offset: int) -> bool:
    """Walk back from ``offset`` to find the nearest markdown heading.

    A section is considered a roadmap zone if that heading line contains any
    of the configured qualifiers. If no preceding heading is found at all the
    section is not considered a roadmap zone.
    """
    if offset <= 0:
        return False
    before = text[:offset]
    for line in reversed(before.split("\n")):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").lower()
        return any(qualifier in heading for qualifier in ROADMAP_QUALIFIERS)
    return False


def _find_all_substrings(text: str, phrase: str) -> list[int]:
    if not phrase:
        return []
    needle = phrase.lower()
    haystack = text.lower()
    found: list[int] = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return found
        found.append(index)
        start = index + max(1, len(needle))


# ---------------------------------------------------------------------------
# Minimal YAML subset parser
#
# Supports exactly what ``docs/claims.yml`` needs:
#   * a top-level mapping;
#   * sequences (block style, items begin with '- ');
#   * scalar values: unquoted, single-quoted, or double-quoted strings; ints;
#     ``true``/``false``; ``null`` and ``~``; empty inline ``[]`` and ``{}``;
#   * 2-space indentation.
#
# It deliberately rejects tabs, anchors, references, flow-style non-empty
# containers, and block scalars (``|``, ``>``).


@dataclass(frozen=True)
class _Token:
    indent: int
    content: str
    line: int


def parse_yaml(text: str) -> Any:
    """Parse a tiny YAML subset. Raises ``ValueError`` on unsupported input."""
    tokens = _tokenize(text)
    if not tokens:
        return None
    value, idx = _parse_node(tokens, 0, tokens[0].indent)
    if idx != len(tokens):
        leftover = tokens[idx]
        raise ValueError(
            f"unexpected content at line {leftover.line}: {leftover.content!r}"
        )
    return value


def _tokenize(text: str) -> list[_Token]:
    out: list[_Token] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise ValueError(f"tabs are not supported (line {line_no})")
        stripped = raw.lstrip(" ")
        indent = len(raw) - len(stripped)
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        content = _strip_inline_comment(stripped)
        if not content:
            continue
        out.append(_Token(indent=indent, content=content, line=line_no))
    return out


def _strip_inline_comment(s: str) -> str:
    in_single = False
    in_double = False
    for i, c in enumerate(s):
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and not in_single and not in_double:
            if i == 0 or s[i - 1] == " ":
                return s[:i].rstrip()
    return s.rstrip()


def _parse_node(tokens: list[_Token], idx: int, indent: int) -> tuple[Any, int]:
    if idx >= len(tokens):
        return None, idx
    cur = tokens[idx]
    if cur.indent != indent:
        raise ValueError(
            f"unexpected indent {cur.indent} (expected {indent}) at line {cur.line}"
        )
    if cur.content.startswith("- ") or cur.content == "-":
        return _parse_sequence(tokens, idx, indent)
    return _parse_mapping(tokens, idx, indent)


def _parse_mapping(tokens: list[_Token], idx: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while idx < len(tokens):
        cur = tokens[idx]
        if cur.indent < indent:
            break
        if cur.indent > indent:
            raise ValueError(
                f"unexpected indent {cur.indent} (expected {indent}) at line {cur.line}"
            )
        if cur.content.startswith("- ") or cur.content == "-":
            break
        key, sep, rest = _split_key_value(cur.content)
        if not sep:
            raise ValueError(f"expected mapping key at line {cur.line}: {cur.content!r}")
        key = key.strip()
        idx += 1
        if rest is not None and rest != "":
            result[key] = _parse_scalar(rest, cur.line)
            continue
        if idx < len(tokens) and tokens[idx].indent > indent:
            value, idx = _parse_node(tokens, idx, tokens[idx].indent)
            result[key] = value
        else:
            result[key] = None
    return result, idx


def _parse_sequence(tokens: list[_Token], idx: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while idx < len(tokens):
        cur = tokens[idx]
        if cur.indent < indent:
            break
        if cur.indent > indent:
            raise ValueError(
                f"unexpected indent {cur.indent} (expected {indent}) at line {cur.line}"
            )
        if not (cur.content.startswith("- ") or cur.content == "-"):
            break
        body = cur.content[1:].lstrip(" ") if cur.content != "-" else ""
        idx += 1
        if not body:
            if idx < len(tokens) and tokens[idx].indent > indent:
                value, idx = _parse_node(tokens, idx, tokens[idx].indent)
                result.append(value)
            else:
                result.append(None)
            continue
        if _has_unquoted_colon(body):
            inline_key, _, inline_value = _split_key_value(body)
            inline_key = inline_key.strip()
            mapping: dict[str, Any] = {}
            if inline_value:
                mapping[inline_key] = _parse_scalar(inline_value, cur.line)
            else:
                # Inline key has no value on this line; the value is the next
                # indented block (deeper than cur.indent + 2).
                if idx < len(tokens) and tokens[idx].indent > cur.indent + 2:
                    value, idx = _parse_node(tokens, idx, tokens[idx].indent)
                    mapping[inline_key] = value
                else:
                    mapping[inline_key] = None
            # Continuation keys for this list item live at cur.indent + 2.
            inner_indent = cur.indent + 2
            while idx < len(tokens):
                cont = tokens[idx]
                if cont.indent != inner_indent:
                    break
                if cont.content.startswith("- ") or cont.content == "-":
                    break
                k, sep, v = _split_key_value(cont.content)
                if not sep:
                    raise ValueError(
                        f"expected mapping key at line {cont.line}: {cont.content!r}"
                    )
                k = k.strip()
                idx += 1
                if v is not None and v != "":
                    mapping[k] = _parse_scalar(v, cont.line)
                else:
                    if idx < len(tokens) and tokens[idx].indent > inner_indent:
                        sub_value, idx = _parse_node(
                            tokens, idx, tokens[idx].indent
                        )
                        mapping[k] = sub_value
                    else:
                        mapping[k] = None
            result.append(mapping)
        else:
            result.append(_parse_scalar(body, cur.line))
    return result, idx


def _split_key_value(content: str) -> tuple[str, str | None, str | None]:
    in_single = False
    in_double = False
    for i, c in enumerate(content):
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == ":" and not in_single and not in_double:
            return content[:i], ":", content[i + 1 :].strip()
    return content, None, None


def _has_unquoted_colon(s: str) -> bool:
    in_single = False
    in_double = False
    for c in s:
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == ":" and not in_single and not in_double:
            return True
    return False


def _parse_scalar(raw: str, line: int) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if s == "null" or s == "~":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if s == "[]":
        return []
    if s == "{}":
        return {}
    if s.startswith('"'):
        if not s.endswith('"') or len(s) < 2:
            raise ValueError(f"unterminated double-quoted string at line {line}")
        return _unescape_double(s[1:-1])
    if s.startswith("'"):
        if not s.endswith("'") or len(s) < 2:
            raise ValueError(f"unterminated single-quoted string at line {line}")
        return s[1:-1].replace("''", "'")
    try:
        return int(s)
    except ValueError:
        return s


def _unescape_double(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "r":
                out.append("\r")
            elif nxt == "\\":
                out.append("\\")
            elif nxt == '"':
                out.append('"')
            else:
                out.append(s[i : i + 2])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)
