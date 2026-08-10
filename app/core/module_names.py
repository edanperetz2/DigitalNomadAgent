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

# The research tools inside the Tool Registry. Same rule as the pipeline modules
# above: the registry built in app/main.py, the architecture diagram and the
# documentation must all name exactly these. The diagram used to list three
# tools that had been deleted (Education Options, Accessibility, Official
# Sources) and omit six that existed, which is what this list exists to prevent.
TOOL_NAMES: tuple[str, ...] = (
    "GeocodingTool",
    "WeatherTool",
    "WikivoyageClimateTool",
    "AmenitiesTool",
    "LocalMobilityTool",
    "PlaceContextTool",
    "TimezoneFitTool",
    "BudgetFitTool",
    "TransportAccessTool",
    "ActivitiesTool",
    "SafetyTool",
    "LanguageTool",
    "TerrainTool",
)


def tool_display_label(tool_name: str) -> str:
    """`WikivoyageClimateTool` -> `Wikivoyage Climate`, for diagrams and prose.

    The class-style name is what the code and the tool results use; the spaced
    form is what a reader sees. Deriving one from the other keeps them from
    drifting apart the way the hand-drawn diagram did.
    """
    stem = tool_name.removesuffix("Tool")
    words: list[str] = []
    for char in stem:
        if char.isupper() and words and words[-1]:
            words.append("")
        if not words:
            words.append("")
        words[-1] += char
    return " ".join(word for word in words if word)
