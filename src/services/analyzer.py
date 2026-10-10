"""
Analyzer service for teacher question classification (T060).

Stateless LLM-based classification using rubric-specific
prompts with structured JSON output.
"""

import logging
from typing import Any, Dict, Optional

from src.api.schemas.scenario_config import AnalysisConfig
from src.prompts.example_templates import generate_examples
from src.services.analysis_invocations import AnalysisCaller
from src.services.role_output_contracts import (
    RuntimeClassification,
    RuntimeGreetings,
)
from src.utils.cache import load_prompt_template

logger = logging.getLogger(__name__)


class Analyzer(AnalysisCaller):
    """Question classification with the legacy normalization policy."""

    def __init__(self, factory, **context):
        super().__init__(factory, **context)
        # Load cached prompt templates (T111 optimization)
        self.prompt_template = load_prompt_template("analysis_prompt.txt")
        self.greeting_template = load_prompt_template("greeting_detection.txt")
        self.greeting_failed = False

    def _normalize_reasoning(self, reasoning: Any) -> dict:
        """
        Normalize reasoning to the slim 2-field structure.

        Drops legacy per-domain blocks (pedagogical/cognitive/contextual)
        if the LLM accidentally returns them.

        Args:
            reasoning: Raw reasoning from LLM (string or dict)

        Returns:
            Dict with exactly two keys: summary, improved_sentence.
        """
        if isinstance(reasoning, str):
            return {"summary": reasoning, "improved_sentence": None}
        if isinstance(reasoning, dict):
            return {
                "summary": reasoning.get("summary", ""),
                "improved_sentence": reasoning.get("improved_sentence"),
            }
        return {
            "summary": str(reasoning) if reasoning else "",
            "improved_sentence": None,
        }

    async def classify_question(
        self,
        question: str,
        analysis: AnalysisConfig,
        context: Optional[str] = None,
        scenario_title: Optional[str] = None,
        misconception_prompt: Optional[str] = None,
        student_profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Classify a teacher question using the frozen rubric.

        Args:
            question: Teacher's question text
            analysis: Frozen evaluation settings and rubric
            context: Optional conversation context (previous messages)
            scenario_title: Optional scenario title for context
            misconception_prompt: Optional misconception being addressed
            student_profile: Optional student profile/persona

        Returns:
            Dict with keys: label, confidence, reasoning

        Raises:
            ValueError: If response format is invalid
            InvocationError: If the common provider invocation fails
        """
        # Generate dynamic few-shot examples
        few_shot_examples = generate_examples(
            [
                dict(name=r.id, criteria=r.criteria, level=r.level)
                for r in analysis.rubric
            ],
            analysis.rubric_description,
        )

        # Build criteria-formatted labels for prompt.
        # Issue #33: include `level` in the prompt so the LLM knows which
        # labels need an `improved_sentence`.
        criteria_map = {
            r.id: f"{r.name}: {r.criteria}" for r in analysis.rubric
        }
        level_map = {r.id: r.level for r in analysis.rubric}

        def _format_label(name: str, criteria: str) -> str:
            level = level_map.get(name)
            level_tag = f" [level={level}]" if level else ""
            base = f"- **{name}**{level_tag}"
            return f"{base}: {criteria}" if criteria else base

        labels_with_criteria = "\n".join(
            _format_label(name, criteria)
            for name, criteria in criteria_map.items()
        )
        label_names = [r.id for r in analysis.rubric]

        # Format prompt with analysis and scenario context
        prompt = self.prompt_template.format(
            rubric_name=analysis.rubric_name,
            rubric_description=(analysis.rubric_description),
            rubric_labels=", ".join(label_names),
            rubric_labels_with_criteria=(labels_with_criteria),
            few_shot_examples=few_shot_examples,
            scenario_title=(scenario_title or "Not specified"),
            misconception_prompt=(misconception_prompt or "Not specified"),
            student_profile=(student_profile or "Not specified"),
            question=question,
            context=context or "No prior context",
        )

        prompt += (
            f"\n평가 맥락\n{analysis.context}\n기대 이해\n{analysis.expected_understanding}"
            f"\n평가 지시\n{analysis.instruction}\n분류 설명\n{analysis.rubric_description}"
            f"\n분류 범주\n{analysis.category_name}"
        )

        def normalize(result):
            # Validate response structure
            if not all(k in result for k in ["label", "confidence"]):
                raise ValueError(
                    "Invalid response format: missing required fields"
                )

            # Validate label is in the rubric
            if result["label"] not in label_names:
                raise ValueError("Unknown rubric ID")

            # Validate confidence range
            confidence = float(result["confidence"])
            if not 0.0 <= confidence <= 1.0:
                logger.warning(
                    f"Invalid confidence {confidence}, clamping to [0,1]"
                )
                result["confidence"] = max(0.0, min(1.0, confidence))

            # Normalize reasoning to structured format
            result["reasoning"] = self._normalize_reasoning(
                result.get("reasoning", "")
            )
            return result

        try:
            result, api_usage = await self.structured(
                prompt,
                RuntimeClassification,
                "classification",
                normalize=normalize,
            )
            if api_usage is not None:
                result["_api_usage"] = api_usage

            return result

        except Exception as e:
            logger.error("Classification failed: %s", e)
            raise

    async def batch_classify(
        self,
        questions: list[str],
        analysis: AnalysisConfig,
        context: Optional[str] = None,
        scenario_title: Optional[str] = None,
        misconception_prompt: Optional[str] = None,
        student_profile: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """
        Classify multiple questions sequentially.

        Args:
            questions: List of teacher questions
            analysis: Frozen evaluation settings and rubric
            context: Optional shared context
            scenario_title: Optional scenario title for context
            misconception_prompt: Optional misconception being addressed
            student_profile: Optional student profile/persona

        Returns:
            List of classification results
        """
        results = []
        for question in questions:
            try:
                result = await self.classify_question(
                    question=question,
                    analysis=analysis,
                    context=context,
                    scenario_title=scenario_title,
                    misconception_prompt=misconception_prompt,
                    student_profile=student_profile,
                )
                results.append(result)
            except Exception as e:
                logger.error(
                    "Failed to classify question '%s': %s", question, e
                )
                # Return default classification on failure
                results.append(
                    {
                        "label": analysis.rubric[0].id,
                        "confidence": 0.0,
                        "reasoning": (f"Classification failed: {e}"),
                    }
                )
        return results

    async def detect_greetings(
        self, messages: list[str]
    ) -> list[Dict[str, Any]]:
        """
        Detect greeting messages using LLM.

        Args:
            messages: List of teacher message contents

        Returns:
            List of dicts with keys: index, is_greeting, reason

        Example:
            [
                {"index": 0, "is_greeting": True, "reason": "Opening greeting"},
                {"index": 1, "is_greeting": False,
                 "reason": "Conceptual question"}
            ]
        """
        if not messages:
            return []

        # Format messages for prompt
        formatted_messages = "\n".join(
            f"{i}. {msg}" for i, msg in enumerate(messages)
        )
        prompt = self.greeting_template.format(messages=formatted_messages)

        try:
            payload, _ = await self.structured(
                prompt, RuntimeGreetings, "greeting"
            )
            results = payload["results"]

            # Ensure all indices are covered
            validated_results = []
            result_map = {r.get("index"): r for r in results}

            for i in range(len(messages)):
                if i in result_map:
                    validated_results.append(result_map[i])
                else:
                    # Default to non-greeting if missing
                    validated_results.append(
                        {
                            "index": i,
                            "is_greeting": False,
                            "reason": "Not classified",
                        }
                    )

            return validated_results

        except Exception as e:
            self.greeting_failed = True
            logger.warning("Greeting detection failed: %s", e)
            # Return safe defaults (assume no greetings)
            return [
                {"index": i, "is_greeting": False, "reason": "Detection failed"}
                for i in range(len(messages))
            ]
