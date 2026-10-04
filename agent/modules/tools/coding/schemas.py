"""Model-visible input schemas for the workspace coding tools."""

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecInput(Input):
    command: str = Field(min_length=1)
    workdir: str | None = None
    timeout_seconds: float = Field(default=120, ge=1, le=600)
    yield_time_ms: int = Field(default=1000, ge=0, le=30000)


class ProcessInput(Input):
    process_id: str


class ProcessReadInput(ProcessInput):
    cursor: int = Field(default=0, ge=0, description="Byte cursor returned by the previous observation. Explicit cursors make reads repeatable.")
    yield_time_ms: int = Field(default=0, ge=0, le=30000)


class ProcessWriteInput(ProcessReadInput):
    text: str = Field(description="Exact stdin text, including any required newline.")


class ReadInput(Input):
    file_path: str
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=2000, ge=1, le=2000)


class ListInput(Input):
    path: str = ""
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=500, ge=1, le=2000)


class EditInput(Input):
    file_path: str
    old_string: str = Field(min_length=1)
    new_string: str
    replace_all: bool = False
    expected_version: str | None = None


class WriteInput(Input):
    file_path: str
    content: str
    append: bool = False
    expected_version: str | None = None


class GlobInput(Input):
    pattern: str = Field(min_length=1)
    path: str = ""
    include_dirs: bool = False
    limit: int = Field(default=500, ge=1, le=5000)


class GrepInput(Input):
    pattern: str = Field(min_length=1)
    path: str = ""
    include: str | None = None
    case_insensitive: bool = False
    fixed_strings: bool = False
    max_results: int = Field(default=100, ge=1, le=1000)


class PatchInput(Input):
    patch_text: str = Field(min_length=1)


class OutputReadInput(Input):
    output_ref: str
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=2000, ge=1, le=2000)
