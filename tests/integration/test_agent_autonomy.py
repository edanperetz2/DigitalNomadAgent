"""Verifies Agentic Research does not run a fixed tool list for every prompt."""

import pytest


async def _stored_tool_names(app_instance) -> set[str]:
    cursor = await app_instance.state.db.conn.execute("SELECT DISTINCT criterion FROM evidence")
    rows = await cursor.fetchall()
    return {row[0] for row in rows}


@pytest.mark.asyncio
async def test_remote_work_stores_no_dedicated_education_evidence(client, app_instance):
    client.post(
        "/api/execute",
        json={
            "prompt": (
                "I want to spend three months somewhere in Europe where I can work remotely, "
                "live without a car, and stay within €1,800 per month."
            )
        },
    )
    tool_names = await _stored_tool_names(app_instance)
    assert "EducationOptionsTool" not in tool_names


@pytest.mark.asyncio
async def test_study_prompt_uses_amenities_for_student_infrastructure(client, app_instance):
    client.post(
        "/api/execute",
        json={
            "prompt": (
                "Recommend a city for a one-semester computer-science exchange. I care about "
                "student life, public transportation, safety, and affordable housing."
            )
        },
    )
    tool_names = await _stored_tool_names(app_instance)
    assert "AmenitiesTool" in tool_names
    assert "EducationOptionsTool" not in tool_names


@pytest.mark.asyncio
async def test_vacation_prompt_stores_weather_and_activity_evidence(client, app_instance):
    client.post(
        "/api/execute",
        json={
            "prompt": (
                "Find a quiet beach destination for two weeks in October, with warm but not "
                "extremely hot weather and good hiking nearby."
            )
        },
    )
    tool_names = await _stored_tool_names(app_instance)
    assert "WeatherTool" in tool_names
    assert "activities" in tool_names


@pytest.mark.asyncio
async def test_study_prompt_does_not_use_weather_by_default(client, app_instance):
    client.post(
        "/api/execute",
        json={
            "prompt": (
                "Recommend a city for a one-semester computer-science exchange. I care about "
                "student life, public transportation, safety, and affordable housing."
            )
        },
    )
    tool_names = await _stored_tool_names(app_instance)
    assert "WeatherTool" not in tool_names
