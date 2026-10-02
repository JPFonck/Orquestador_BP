from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

POLICIES_PATH = Path(__file__).with_name("policies.yaml")


class DocumentRequirement(BaseModel):
    code: str
    name: str
    description: str


class ProductPolicy(BaseModel):
    display_name: str
    min_age: int
    base_documents: list[DocumentRequirement]


class Thresholds(BaseModel):
    identity_min_confidence: float
    biometric_retry_min_confidence: float


class BankPolicies(BaseModel):
    version: str
    thresholds: Thresholds
    escalation_risk_levels: list[str]
    edd_risk_levels: list[str]
    products: dict[str, ProductPolicy]
    risk_documents: dict[str, list[DocumentRequirement]]

    def is_supported(self, product: str) -> bool:
        return product in self.products

    def product(self, product: str) -> ProductPolicy | None:
        return self.products.get(product)


@lru_cache
def load_policies(path: Path = POLICIES_PATH) -> BankPolicies:
    with open(path, encoding="utf-8") as f:
        return BankPolicies.model_validate(yaml.safe_load(f))
