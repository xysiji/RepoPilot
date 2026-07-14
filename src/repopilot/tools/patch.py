"""The sole model-visible P4 side-effect proposal tool."""

from langchain_core.tools import BaseTool, StructuredTool

from repopilot.tools.contracts import (
    ProposePatchInput,
    ToolErrorCode,
    ToolExecutionPhase,
    ToolFailureCategory,
    failed_result,
)


def build_patch_tool() -> BaseTool:
    """Expose proposal schema without exposing any file-writing callable."""

    def propose_patch(path: str, new_content: str, rationale: str) -> str:
        """Return a defensive failure if invoked outside the approval executor."""

        del path, new_content, rationale
        return failed_result(
            phase=ToolExecutionPhase.PREPARATION,
            category=ToolFailureCategory.APPROVAL,
            code=ToolErrorCode.APPROVAL_REQUIRED,
            message="Patch proposals must pass through the human approval workflow.",
        ).stable_json()

    return StructuredTool.from_function(
        func=propose_patch,
        name="propose_patch",
        description=(
            "Propose complete replacement content for one existing UTF-8 text file. "
            "This call never modifies a file: the system computes a complete diff and a human "
            "must approve it first. File creation, deletion, rename, multiple-file changes, "
            "binary edits, links, and command execution are not supported."
        ),
        args_schema=ProposePatchInput,
    )
