"""
api/routes/review.py — read a test and say how to make it better.

POST /review  {steps, platform, environment} → findings, most serious first.

The checks are deterministic (ai_flow_builder/review.py): same answer every
time, no model call, milliseconds. Each finding carries a `fix` where a
replacement can be derived, so the editor can offer to apply it rather than
leaving the author to retype the step from a description.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from ai_flow_builder.review import as_dicts, review

router = APIRouter(tags=["review"])


class ReviewRequest(BaseModel):
    steps: list[str]
    platform: str = "website"
    environment: str = ""
    flow_name: str = ""


@router.post("/review")
def review_steps(body: ReviewRequest):
    findings = review(body.steps, platform=body.platform,
                      environment=body.environment, flow_name=body.flow_name)
    from ai_flow_builder.review import BUCKET_LABEL, BUCKETS

    counts = {b: 0 for b in BUCKETS}
    for f in findings:
        counts[f.bucket] = counts.get(f.bucket, 0) + 1
    return {"findings": as_dicts(findings), "counts": counts,
            "labels": BUCKET_LABEL, "buckets": list(BUCKETS),
            "total": len(findings)}
