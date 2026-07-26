"""Golden-set evaluation harness: a fixed representative prompt set plus a
structural (not exact-text) comparison scorer, for regression-checking the
pipeline and for tuning the LLM-calling modules' prompts once a real
provider is in use. Safe to run under MOCK_LLM (the default) at zero cost.
"""
