from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TierPolicy:
    default_model: str
    tiers: dict[str, str]

    @staticmethod
    def from_json(obj: Any) -> TierPolicy:
        """Parse tier policy from JSON object.

        Args:
            obj: JSON object with 'default_model' and 'tiers' fields

        Returns:
            TierPolicy instance

        Raises:
            ValueError: If policy structure is invalid
        """
        if not isinstance(obj, dict):
            raise ValueError(
                f"Policy must be a JSON object, got: {type(obj).__name__}"
            )

        default_model = obj.get("default_model")
        if not isinstance(default_model, str):
            raise ValueError(
                f"policy.default_model must be a string, "
                f"got: {type(default_model).__name__}"
            )
        if not default_model or not default_model.strip():
            raise ValueError("policy.default_model cannot be empty")

        tiers_raw = obj.get("tiers")
        if tiers_raw is None:
            tiers_raw = {}
        if not isinstance(tiers_raw, dict):
            raise ValueError(
                f"policy.tiers must be an object, got: {type(tiers_raw).__name__}"
            )

        tiers = {}
        for k, v in tiers_raw.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError(
                    f"policy.tiers keys and values must be strings, "
                    f"got: {type(k).__name__} -> {type(v).__name__}"
                )
            if not k.strip() or not v.strip():
                raise ValueError("policy.tiers keys and values cannot be empty")
            tiers[k.strip()] = v.strip()

        return TierPolicy(default_model=default_model.strip(), tiers=tiers)

    def model_for(self, tier: str) -> str:
        """Get model for a given tier.

        Args:
            tier: Tier name

        Returns:
            Model name (falls back to default_model if tier not found)
        """
        return self.tiers.get(tier) or self.default_model
