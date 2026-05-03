"""Eval: a large/binary tool result is flagged as unsafe by the reviewer."""
from __future__ import annotations

from rulence.evals import Eval
from rulence.review import ToolResultReviewer


def run() -> dict:
    reviewer = ToolResultReviewer()
    huge = "binary noise " * 10_000
    review = reviewer.review(
        tool_name="Bash",
        tool_input={"command": "cat large.bin"},
        tool_output=huge,
    )
    flagged = review.status in {"warn", "unsafe"} or not review.safe_for_model_context
    return {
        "status": "pass" if flagged else "fail",
        "detail": (
            f"status={review.status!r} safe={review.safe_for_model_context} "
            f"orig_tokens={review.original_token_estimate}"
        ),
        "metrics": {
            "large_tool_result_flagged": bool(flagged),
            "original_token_estimate": int(review.original_token_estimate),
            "filtered_token_estimate": int(review.filtered_token_estimate),
        },
    }


EVAL = Eval(
    eval_id="token.large_tool_result_flagged",
    category="token",
    description="ToolResultReviewer flags a very large tool output as unsafe / unsafe-for-context.",
    claim_ids=("token_accounting", "posttooluse_observed_redaction"),
    runner=run,
)
