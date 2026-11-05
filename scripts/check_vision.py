"""Check the known sample only. Passing does not establish corpus accuracy."""
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field


class CableObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cable_type: str | None
    cores: int | None = Field(ge=1)
    length_km: float | None = Field(ge=0)
    visible_station_names: list[str]


root = Path(__file__).resolve().parents[1]
result = json.loads((root / "outputs/verification/vision.json").read_text())
parsed = CableObservation.model_validate(result["parsed"])
assert parsed.cable_type == "OPGW", parsed
assert parsed.cores == 72, parsed
assert parsed.length_km == 72, parsed
names = " ".join(parsed.visible_station_names)
assert "项目A" in names and "丁站" in names, parsed
summary = {"schema_valid": True, "sample_expected_fields_match": True,
           "sample_only_not_accuracy_benchmark": True}
(root / "outputs/verification/vision-check.json").write_text(json.dumps(summary, indent=2))
print(summary)
