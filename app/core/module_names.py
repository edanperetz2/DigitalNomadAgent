"""Single source of truth for canonical DigitalNomadAgent architecture module names.

These exact strings must be used everywhere the architecture is referenced:
source code, the architecture diagram, /api/agent_info, LLM-call tracing,
documentation, and tests. Do not duplicate these strings elsewhere.
"""

REQUEST_INTERPRETER = "Request Interpreter"
AGENTIC_RESEARCH = "Agentic Research"
TOOL_REGISTRY = "Tool Registry"
EVIDENCE_MEMORY = "Evidence Memory"
DYNAMIC_EVALUATION = "Dynamic Evaluation"
RECOMMENDATION_VALIDATOR = "Recommendation Validator"
RECOMMENDATION_GENERATOR = "Recommendation Generator"

ALL_MODULES: tuple[str, ...] = (
    REQUEST_INTERPRETER,
    AGENTIC_RESEARCH,
    TOOL_REGISTRY,
    EVIDENCE_MEMORY,
    DYNAMIC_EVALUATION,
    RECOMMENDATION_VALIDATOR,
    RECOMMENDATION_GENERATOR,
)

# Modules that are ever allowed to appear in an /api/execute `steps` entry
# (i.e. the only modules that make LLM calls, via TracedLLMClient).
LLM_CALLING_MODULES: tuple[str, ...] = (
    REQUEST_INTERPRETER,
    AGENTIC_RESEARCH,
    DYNAMIC_EVALUATION,
    RECOMMENDATION_GENERATOR,
)
