"""The semantic stage graph, independent of provider session ownership."""
from dataclasses import dataclass


@dataclass(frozen=True)
class StageDefinition:
    number: int
    category: str
    dependencies: tuple[int, ...] = ()


STAGES = {n: StageDefinition(n, category, deps) for n, category, deps in (
    (0, "source_prerequisite", ()), (1, "analysis", ()),
    (2, "translation", ()), (3, "translation", (2,)),
    (4, "translation", (2, 3)), (5, "translation", (3, 4)),
)}


def translation_input(number, common, sentences, accepted):
    """Both full and targeted execution supply the same canonical dependencies."""
    stage = STAGES[number]
    result = dict(common)
    if number in (2, 4):
        result["SOURCE_SENTENCES"] = sentences
    for dependency in stage.dependencies:
        result[{2: "SEMANTIC_AUDIT", 3: "POLISH_DRAFT", 4: "CORRECTION_LEDGER"}[dependency]] = accepted[dependency]
    return result
