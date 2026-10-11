"""S4 analysis schemas; original messages and frozen rubric are authoritative."""

from collections import Counter
from typing import Annotated, Literal

from pydantic import (
    Field,
    StrictInt,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from src.services.invocation_types import InvocationError
from src.services.role_output_contracts import RoleOutput

Quote = Annotated[str, Field(min_length=1, max_length=200)]
Reason = Annotated[str, Field(min_length=1, max_length=300)]


class AnalysisItem(RoleOutput):
    @model_validator(mode="after")
    def nonblank(self):
        if any(
            isinstance(value, str) and not value.strip()
            for value in self.model_dump().values()
        ):
            raise InvocationError("invalid_output")
        return self


class Evidence(AnalysisItem):
    message_id: int
    quote: Quote


class MessageClassification(Evidence):
    disposition: Literal["classified", "non_analyzable", "unclassified"]
    rubric_id: str | None
    reason: Reason


class Finding(AnalysisItem):
    kind: Literal["maintained", "changed", "deviated", "insufficient_evidence"]
    claim: Reason
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


class AnalysisStrength(Evidence):
    reason: Reason


class AnalysisImprovement(Evidence):
    missed_reason: Reason
    alternative_question: Quote
    alternative_reason: Reason


class AnalysisCoaching(Evidence):
    role: Literal["teacher", "student"]
    marker: Literal["good_moment", "missed_moment", "key_clue"]
    note: Reason


class MergeAnalysisOutput(RoleOutput):
    schema_version: StrictInt = Field(ge=2, le=2)
    misconception_findings: list[Finding] = Field(max_length=10)
    brief_feedback: list[Reason] = Field(min_length=1, max_length=3)
    strengths: list[AnalysisStrength] = Field(max_length=5)
    improvements: list[AnalysisImprovement] = Field(max_length=5)
    dialogue_coaching: list[AnalysisCoaching] = Field(max_length=10)

    @model_validator(mode="before")
    @classmethod
    def verified_sections(cls, value, info: ValidationInfo):
        context = info.context if info.context is not None else {}
        if (
            not isinstance(value, dict)
            or value.keys() != cls.model_fields.keys()
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 2
            or any(
                not isinstance(value[key], list)
                for key in value
                if key != "schema_version"
            )
        ):
            raise InvocationError("invalid_output")
        feedback = value["brief_feedback"]
        if not feedback or not any(
            isinstance(t, str) and t.strip() for t in feedback
        ):
            raise InvocationError("empty_response")
        if len(feedback) > 3:
            raise InvocationError("invalid_output")
        valid_feedback = [
            t
            for t in feedback
            if isinstance(t, str) and t.strip() and len(t) <= 300
        ]
        if not valid_feedback:
            raise InvocationError("invalid_output")
        clean = {"schema_version": 2, "brief_feedback": valid_feedback}
        errors = (
            [dict(section="brief_feedback", code="invalid_output")]
            if len(valid_feedback) != len(feedback)
            else []
        )
        classifications = value.get("message_classifications", [])
        duplicates = {
            mid
            for mid, count in Counter(
                item.get("message_id")
                for item in classifications
                if isinstance(item, dict)
                and type(item.get("message_id")) is int
            ).items()
            if count > 1
        }
        for section, (model, limit) in SECTION_TYPES.items():
            if section not in value:
                continue
            clean[section] = []
            if (limit is not None and len(value[section]) > limit) or (
                section == "message_classifications"
                and not context.get("classification_enabled", True)
                and value[section]
            ):
                errors.append(dict(section=section, code="invalid_output"))
                continue
            for raw in value[section]:
                try:
                    item = model.model_validate(raw, strict=True)
                    validate_item(section, item, context, duplicates)
                    clean[section].append(item.model_dump())
                except (ValidationError, InvocationError) as error:
                    code = (
                        error.code
                        if isinstance(error, InvocationError)
                        else "invalid_output"
                    )
                    errors.append(dict(section=section, code=code))
                    if section == "message_classifications" and isinstance(
                        raw, dict
                    ):
                        mid = raw.get("message_id")
                        if (
                            type(mid) is int
                            and mid in context.get("owned_message_ids", [mid])
                            and any(
                                m["id"] == mid and m["role"] == "teacher"
                                for m in context.get("messages", [])
                            )
                        ):
                            context.setdefault(
                                "invalid_message_ids", []
                            ).append(mid)
        if "message_classifications" in clean and context.get(
            "classification_enabled", True
        ):
            supplied = {
                item["message_id"] for item in clean["message_classifications"]
            }
            if any(
                m["role"] == "teacher"
                and m["id"] not in supplied
                and m["id"] in context.get("owned_message_ids", [m["id"]])
                for m in context.get("messages", [])
            ):
                errors.append(
                    dict(
                        section="message_classifications", code="invalid_output"
                    )
                )
        context["analysis_errors"] = errors
        return clean

    @model_validator(mode="after")
    def validated(self, info: ValidationInfo):
        if info.context is not None:
            info.context["validated_analysis"] = self.model_dump()
        return self


class UnifiedAnalysisOutput(MergeAnalysisOutput):
    message_classifications: list[MessageClassification]


SECTION_TYPES = {
    "message_classifications": (MessageClassification, None),
    "misconception_findings": (Finding, 10),
    "strengths": (AnalysisStrength, 5),
    "improvements": (AnalysisImprovement, 5),
    "dialogue_coaching": (AnalysisCoaching, 10),
}


def validate_item(section, item, context, duplicates):
    messages = context.get("messages", [])
    by_id = {m["id"]: m for m in messages}
    order = {m["id"]: i for i, m in enumerate(messages)}

    def reference(evidence, role=None):
        message = by_id.get(evidence.message_id)
        if (
            message is None
            or (role is not None and message["role"] != role)
            or evidence.quote not in message["content"]
        ):
            raise InvocationError("invalid_reference")
        if (
            "allowed_evidence" in context
            and dict(message_id=evidence.message_id, quote=evidence.quote)
            not in context["allowed_evidence"]
        ):
            raise InvocationError("invalid_reference")

    if section == "misconception_findings":
        for evidence in item.evidence:
            reference(evidence, "student")
        if "owned_message_ids" in context:
            owned = context["owned_message_ids"]
            if not any(e.message_id in owned for e in item.evidence) or (
                item.kind != "changed"
                and any(e.message_id not in owned for e in item.evidence)
            ):
                raise InvocationError("invalid_reference")
        if item.kind == "changed":
            ids = [e.message_id for e in item.evidence]
            if (
                len(set(ids)) < 2
                or len(set(ids)) != len(ids)
                or ids != sorted(ids, key=order.get)
            ):
                raise InvocationError("invalid_reference")
    else:
        if (
            "owned_message_ids" in context
            and item.message_id not in context["owned_message_ids"]
        ):
            raise InvocationError("invalid_reference")
        role = (
            "teacher"
            if section in {"strengths", "message_classifications"}
            else item.role if section == "dialogue_coaching" else None
        )
        reference(item, role)
    if section == "message_classifications":
        if (
            item.message_id in duplicates
            or (
                item.disposition == "classified"
                and item.rubric_id not in context.get("labels", {})
            )
            or (item.disposition != "classified" and item.rubric_id is not None)
        ):
            raise InvocationError("invalid_reference")
