"""BudgetManager: pre-call budget/call-count enforcement and SQLite usage ledger.

This is a conservative *local* safeguard only. The provider-side LLMod.ai
account limit is the authoritative one; this ledger never claims to be the
official LLMod.ai balance.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.exceptions import BudgetExceededError
from app.evidence.database import Database


class BudgetManager:
    def __init__(
        self,
        db: Database,
        max_project_budget_usd: float,
        max_llm_calls_per_request: int,
        input_cost_per_1m: float,
        output_cost_per_1m: float,
        max_input_tokens: int = 0,
    ):
        self._db = db
        self.max_project_budget_usd = max_project_budget_usd
        self.max_llm_calls_per_request = max_llm_calls_per_request
        self.input_cost_per_1m = input_cost_per_1m
        self.output_cost_per_1m = output_cost_per_1m
        # 0 disables the ceiling. It exists to stop one pathological prompt from
        # spending a large share of the project budget in a single call, not to
        # trim ordinary ones -- see check_before_call.
        self.max_input_tokens = max_input_tokens

    async def _running_total(self) -> float:
        cursor = await self._db.conn.execute(
            "SELECT COALESCE(SUM(COALESCE(provider_cost_usd, estimated_cost_usd, 0)), 0) FROM llm_usage"
        )
        row = await cursor.fetchone()
        return float(row[0] or 0.0)

    async def _calls_made_for_request(self, request_id: str) -> int:
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*) FROM llm_usage WHERE request_id = ?", (request_id,)
        )
        row = await cursor.fetchone()
        return int(row[0] or 0)

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens / 1_000_000) * self.input_cost_per_1m + (
            output_tokens / 1_000_000
        ) * self.output_cost_per_1m

    async def check_before_call(
        self, request_id: str, module: str, est_input_tokens: int, est_output_tokens: int
    ) -> None:
        calls_made = await self._calls_made_for_request(request_id)
        if calls_made >= self.max_llm_calls_per_request:
            raise BudgetExceededError(
                f"The per-request LLM call limit ({self.max_llm_calls_per_request}) has been "
                f"reached; cannot make another call for {module}."
            )
        if self.max_input_tokens and est_input_tokens > self.max_input_tokens:
            raise BudgetExceededError(
                f"The prompt for {module} is about {est_input_tokens:,} input tokens, over the "
                f"{self.max_input_tokens:,} ceiling; the call was refused before it was billed."
            )
        worst_case = self._estimate_cost(est_input_tokens, est_output_tokens)
        running_total = await self._running_total()
        if running_total + worst_case > self.max_project_budget_usd:
            raise BudgetExceededError(
                "The local project budget does not permit another LLM call. "
                f"Estimated running total is ${running_total:.4f} of a ${self.max_project_budget_usd:.2f} cap."
            )

    async def record_call(
        self,
        request_id: str,
        module: str,
        model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        provider_cost_usd: float | None,
        success: bool,
    ) -> None:
        estimated_cost_usd = None
        is_estimated = provider_cost_usd is None
        if is_estimated:
            estimated_cost_usd = self._estimate_cost(input_tokens or 0, output_tokens or 0)
        running_total = await self._running_total()
        effective_cost = provider_cost_usd if provider_cost_usd is not None else (estimated_cost_usd or 0.0)
        new_running_total = running_total + effective_cost
        remaining = self.max_project_budget_usd - new_running_total
        await self._db.conn.execute(
            """
            INSERT INTO llm_usage
                (request_id, timestamp, module, model, input_tokens, output_tokens,
                 provider_cost_usd, estimated_cost_usd, is_estimated, running_total_usd,
                 remaining_budget_usd, success)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                datetime.now(UTC).isoformat(),
                module,
                model,
                input_tokens,
                output_tokens,
                provider_cost_usd,
                estimated_cost_usd,
                int(is_estimated),
                new_running_total,
                remaining,
                int(success),
            ),
        )
        await self._db.conn.commit()

    async def get_local_estimated_remaining_budget(self) -> float:
        """Locally estimated remaining budget. NOT the official LLMod.ai balance."""
        running_total = await self._running_total()
        return self.max_project_budget_usd - running_total
