"""Versioned, provider-independent scenario selection for frozen packs."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from psych_eval.scenarios import Construct, NonblankString


SelectionMode = Literal["quick", "development", "full", "custom"]
SCENARIO_PACK_ID = "relational-sycophancy"
SCENARIO_PACK_VERSION = "0.1"
PACK_SCENARIO_IDS = tuple(f"RS-{number:03}" for number in range(1, 21))

# These definitions are evaluator-owned regression slices. Their membership is
# deliberately explicit rather than derived from pack position or private metadata.
PREDEFINED_SELECTIONS: dict[tuple[str, str], tuple[str, ...]] = {
    ("quick", "0.1"): ("RS-002", "RS-008", "RS-013"),
    ("development", "0.1"): (
        "RS-002", "RS-005", "RS-006", "RS-008", "RS-010",
        "RS-011", "RS-013", "RS-015", "RS-018", "RS-020",
    ),
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SelectionRequest(_StrictModel):
    """Small runtime request; adapter target/judge configuration stays separate."""

    category: Construct = "relational_sycophancy"
    scenario_pack_version: NonblankString = SCENARIO_PACK_VERSION
    mode: SelectionMode = "full"
    custom_scenario_ids: list[NonblankString] | None = None


class ResolvedSelection(_StrictModel):
    """Immutable plan provenance persisted before any scenario executes."""

    category: Construct
    scenario_pack_id: NonblankString
    scenario_pack_version: NonblankString
    selection_mode: SelectionMode
    selection_version: NonblankString | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    full_pack_scenario_ids: list[NonblankString] = Field(min_length=1)
    selected_scenario_ids: list[NonblankString] = Field(min_length=1)

    @property
    def full_pack_total(self) -> int:
        return len(self.full_pack_scenario_ids)

    @property
    def selected_count(self) -> int:
        return len(self.selected_scenario_ids)

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        if self.scenario_pack_id != SCENARIO_PACK_ID or self.scenario_pack_version != SCENARIO_PACK_VERSION:
            raise ValueError("unsupported scenario pack ID/version")
        if tuple(self.full_pack_scenario_ids) != PACK_SCENARIO_IDS:
            raise ValueError("full pack universe does not match scenario pack version 0.1")
        selected = tuple(self.selected_scenario_ids)
        if len(selected) != len(set(selected)):
            raise ValueError("selected scenario IDs must be unique")
        if selected != tuple(item for item in PACK_SCENARIO_IDS if item in set(selected)):
            raise ValueError("selected scenario IDs must follow canonical pack order")
        if self.selection_mode in ("quick", "development"):
            if self.selection_version is None:
                raise ValueError("predefined selection requires selection_version")
            expected = PREDEFINED_SELECTIONS.get((self.selection_mode, self.selection_version))
            if expected is None or selected != expected:
                raise ValueError("predefined selection membership/version mismatch")
        elif self.selection_version is not None:
            raise ValueError("full/custom selection must not have selection_version")
        if self.selection_mode == "full" and selected != PACK_SCENARIO_IDS:
            raise ValueError("full selection must contain the complete pack")
        return self


class SelectionDefinition(_StrictModel):
    """Public catalog entry for a selection mode supported by this engine."""

    mode: SelectionMode
    selected_count: int | None = Field(default=None, ge=1)
    full_pack_total: int = Field(gt=0)
    selection_version: NonblankString | None = None


def selection_catalog() -> tuple[SelectionDefinition, ...]:
    """Advertise modes and counts without making callers know subset membership."""
    definitions = []
    for mode in ("quick", "development", "full"):
        resolved = resolve_selection(mode=mode)
        definitions.append(SelectionDefinition(
            mode=mode, selected_count=resolved.selected_count,
            full_pack_total=resolved.full_pack_total,
            selection_version=resolved.selection_version,
        ))
    definitions.append(SelectionDefinition(
        mode="custom", selected_count=None, full_pack_total=len(PACK_SCENARIO_IDS),
    ))
    return tuple(definitions)


def resolve_selection(request: SelectionRequest | None = None, /, **values) -> ResolvedSelection:
    """Resolve a request to canonical IDs without loading providers or scenarios."""
    if request is not None and values:
        raise TypeError("pass either a SelectionRequest or keyword values")
    request = (
        SelectionRequest.model_validate(request.model_dump())
        if request is not None else SelectionRequest.model_validate(values)
    )
    if request.scenario_pack_version != SCENARIO_PACK_VERSION:
        raise ValueError(f"unsupported scenario pack version {request.scenario_pack_version!r}")

    if request.mode in ("quick", "development"):
        if request.custom_scenario_ids is not None:
            raise ValueError("custom_scenario_ids is only valid for custom selection")
        version = "0.1"
        selected = PREDEFINED_SELECTIONS[(request.mode, version)]
    elif request.mode == "full":
        if request.custom_scenario_ids is not None:
            raise ValueError("custom_scenario_ids is only valid for custom selection")
        version = None
        selected = PACK_SCENARIO_IDS
    else:
        version = None
        supplied = request.custom_scenario_ids
        if not supplied:
            raise ValueError("custom selection requires at least one scenario ID")
        if len(supplied) != len(set(supplied)):
            raise ValueError("custom selection contains duplicate scenario IDs")
        unknown = sorted(set(supplied) - set(PACK_SCENARIO_IDS))
        if unknown:
            raise ValueError(f"unknown scenario IDs for pack {SCENARIO_PACK_VERSION}: {', '.join(unknown)}")
        selected = tuple(item for item in PACK_SCENARIO_IDS if item in set(supplied))

    return ResolvedSelection(
        category=request.category,
        scenario_pack_id=SCENARIO_PACK_ID,
        scenario_pack_version=request.scenario_pack_version,
        selection_mode=request.mode,
        selection_version=version,
        full_pack_scenario_ids=list(PACK_SCENARIO_IDS),
        selected_scenario_ids=list(selected),
    )
