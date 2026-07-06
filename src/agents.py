"""Declarative identities and boundaries for every specialist agent."""

# Import dataclass for a small immutable-looking configuration object.
from dataclasses import dataclass

# Import the specialist instructions.
from .prompt import (
    CANDIDATE_REGISTRY_PROMPT,
    CANDIDATE_OUTREACH_PROMPT,
    CODEBASE_PROMPT,
    DATA_SCIENCE_PROMPT,
    RESEARCH_PROMPT,
    RESUME_INGESTION_PROMPT,
    RESUME_SECURITY_GUARD_PROMPT,
    RESUME_SCREENING_PROMPT,
    EMAIL_DELIVERY_PROMPT,
)


# Describe everything the reusable runtime needs to run one specialist.
@dataclass(frozen=True)
class AgentSpec:
    """Configuration for one stateless specialist."""

    # Stable identifier used in traces and delegation schemas.
    name: str
    # Short capability description used by the coordinator.
    description: str
    # Specialist-only system instructions.
    instructions: str
    # Names of low-level tools this specialist may execute.
    tool_names: tuple[str, ...]
    # Bounded number of tool-selection rounds.
    max_tool_rounds: int = 2
    # Maximum serialized evidence returned to the coordinator.
    output_size_limit: int = 16_000
    # Explicit peers that may receive control through a handoff.
    handoff_targets: tuple[str, ...] = ()
    # Protocol message types accepted by this agent.
    accepted_message_types: tuple[str, ...] = ("task_request", "handoff_request")


# Give live-data retrieval to one focused specialist.
RESEARCH_AGENT = AgentSpec(
    name="research_agent",
    description="Finds current information and weather using web and weather tools.",
    instructions=RESEARCH_PROMPT,
    tool_names=("get_weather", "web_search"),
    handoff_targets=("codebase_agent", "data_science_agent"),
)

# Give repository access only to the codebase specialist.
CODEBASE_AGENT = AgentSpec(
    name="codebase_agent",
    description="Answers repository questions using grounded grep evidence.",
    instructions=CODEBASE_PROMPT,
    tool_names=("grep_code",),
    handoff_targets=("research_agent", "data_science_agent"),
)

# Give arbitrary sandboxed computation only to the data-science specialist.
DATA_SCIENCE_AGENT = AgentSpec(
    name="data_science_agent",
    description="Performs calculations, analysis, and chart generation in Docker.",
    instructions=DATA_SCIENCE_PROMPT,
    tool_names=("run_python",),
    handoff_targets=("research_agent", "codebase_agent"),
)

RESUME_INGESTION_AGENT = AgentSpec(
    name="resume_ingestion_agent",
    description="Lists and extracts PDF resumes from the configured Drive folder.",
    instructions=RESUME_INGESTION_PROMPT,
    tool_names=("list_resume_pdfs", "extract_resume_pdf"),
    handoff_targets=("resume_screening_agent",),
)

RESUME_SECURITY_GUARD_AGENT = AgentSpec(
    name="resume_security_guard_agent",
    description="Classifies indirect prompt-injection risk in isolated resume chunks.",
    instructions=RESUME_SECURITY_GUARD_PROMPT,
    tool_names=(),
    handoff_targets=("resume_ingestion_agent",),
)

RESUME_SCREENING_AGENT = AgentSpec(
    name="resume_screening_agent",
    description="Scores anonymized resume evidence against the Full-Stack AI rubric.",
    instructions=RESUME_SCREENING_PROMPT,
    tool_names=("read_screening_criteria",),
    handoff_targets=("candidate_registry_agent",),
)

CANDIDATE_REGISTRY_AGENT = AgentSpec(
    name="candidate_registry_agent",
    description="Writes validated screening decisions to Google Sheets.",
    instructions=CANDIDATE_REGISTRY_PROMPT,
    tool_names=("write_screening_results",),
    handoff_targets=("candidate_outreach_agent",),
)

CANDIDATE_OUTREACH_AGENT = AgentSpec(
    name="candidate_outreach_agent",
    description="Prepares personalized interview outreach without sending authority.",
    instructions=CANDIDATE_OUTREACH_PROMPT,
    tool_names=("prepare_outreach_email",),
    handoff_targets=("email_delivery_agent",),
)

EMAIL_DELIVERY_AGENT = AgentSpec(
    name="email_delivery_agent",
    description="Sends only an immutable human-approved outreach draft.",
    instructions=EMAIL_DELIVERY_PROMPT,
    tool_names=("send_approved_email",),
)

# Provide one registry for validated coordinator delegation.
AGENT_SPECS = {
    RESEARCH_AGENT.name: RESEARCH_AGENT,
    CODEBASE_AGENT.name: CODEBASE_AGENT,
    DATA_SCIENCE_AGENT.name: DATA_SCIENCE_AGENT,
    RESUME_INGESTION_AGENT.name: RESUME_INGESTION_AGENT,
    RESUME_SECURITY_GUARD_AGENT.name: RESUME_SECURITY_GUARD_AGENT,
    RESUME_SCREENING_AGENT.name: RESUME_SCREENING_AGENT,
    CANDIDATE_REGISTRY_AGENT.name: CANDIDATE_REGISTRY_AGENT,
    CANDIDATE_OUTREACH_AGENT.name: CANDIDATE_OUTREACH_AGENT,
    EMAIL_DELIVERY_AGENT.name: EMAIL_DELIVERY_AGENT,
}

# Map model-visible delegation tools to their specialist identities.
DELEGATION_TO_AGENT = {
    "ask_research_agent": RESEARCH_AGENT.name,
    "ask_codebase_agent": CODEBASE_AGENT.name,
    "ask_data_science_agent": DATA_SCIENCE_AGENT.name,
}

# Describe delegation tools independently from low-level application tools.
DELEGATION_DECLARATIONS = [
    {
        "name": tool_name,
        "description": AGENT_SPECS[agent_name].description,
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Focused work request for this specialist.",
                },
                "context": {
                    "type": "string",
                    "description": "Brief relevant context, dependencies, or prior specialist findings.",
                },
            },
            "required": ["task"],
        },
    }
    # Create one explicit agent-as-tool declaration per specialist.
    for tool_name, agent_name in DELEGATION_TO_AGENT.items()
]
