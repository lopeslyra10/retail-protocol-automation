from __future__ import annotations

import re
from datetime import date, datetime
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_os(value: str | int | None) -> str | None:
    """Mantém somente os dígitos da OS; retorna None quando não há número."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


def normalize_category(value: object) -> str:
    """Normaliza uma categoria livre usada para agrupar perfis de quantidade."""
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        raise ValueError("A categoria da lista é obrigatória.")
    if len(cleaned) > 60:
        raise ValueError("A categoria da lista deve ter no máximo 60 caracteres.")
    return cleaned


class Store(BaseModel):
    id: str
    order: int = Field(ge=1)
    name: str
    aliases: list[str] = Field(default_factory=list)
    address: str
    city: str
    state: str = Field(default="SP", min_length=2, max_length=2)
    default_quantity: int = Field(default=100, gt=0)
    active: bool = True


class QuantityProfile(BaseModel):
    """Uma versão nomeada de quantidades, disponível para qualquer tabloide."""

    id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(min_length=3, max_length=120)
    category: str
    quantities: dict[str, int]
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    active: bool = True

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category_field(cls, value: object) -> str:
        return normalize_category(value)

    @field_validator("name", "description")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("quantities")
    @classmethod
    def validate_quantities(cls, value: dict[str, int]) -> dict[str, int]:
        if not value:
            raise ValueError("A lista de quantidades não pode ficar vazia.")
        normalized: dict[str, int] = {}
        for store_id, quantity in value.items():
            number = int(quantity)
            if number <= 0:
                raise ValueError(f"A quantidade da loja {store_id} deve ser maior que zero.")
            normalized[str(store_id)] = number
        return normalized

    def quantity_for(self, store_id: str) -> int:
        try:
            return self.quantities[store_id]
        except KeyError as exc:
            raise KeyError(f"A loja {store_id} não existe na lista {self.name}.") from exc

    def validate_store_ids(self, valid_store_ids: set[str]) -> None:
        profile_store_ids = set(self.quantities)
        unknown = sorted(profile_store_ids - valid_store_ids)
        if unknown:
            raise ValueError(
                f"A lista {self.name} contém lojas desconhecidas: {', '.join(unknown)}."
            )


class ProtocolItem(BaseModel):
    """Um tabloide e a lista de quantidades escolhida para ele."""

    id: str = Field(
        default_factory=lambda: f"tab_{uuid4().hex[:16]}",
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
    )
    description: str = Field(min_length=1, max_length=160)
    quantity_profile_id: str = Field(min_length=3, max_length=80)
    quantity_profile_name: str = Field(min_length=3, max_length=120)
    quantities: dict[str, int]

    @field_validator("description", "quantity_profile_name")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("O texto não pode ficar vazio.")
        return cleaned

    @field_validator("quantities")
    @classmethod
    def validate_quantities(cls, value: dict[str, int]) -> dict[str, int]:
        if not value:
            raise ValueError("O tabloide precisa de uma lista de quantidades.")
        normalized: dict[str, int] = {}
        for store_id, quantity in value.items():
            number = int(quantity)
            if number <= 0:
                raise ValueError(f"A quantidade da loja {store_id} deve ser maior que zero.")
            normalized[str(store_id)] = number
        return normalized

    def quantity_for(self, store_id: str) -> int:
        try:
            return self.quantities[store_id]
        except KeyError as exc:
            raise KeyError(
                f"A loja {store_id} não existe na lista {self.quantity_profile_name}."
            ) from exc

    def applies_to(self, store_id: str) -> bool:
        """Indica se o endereço faz parte da lista deste tabloide."""
        return store_id in self.quantities


class Campaign(BaseModel):
    """Um protocolo sem limites artificiais de OS ou de tabloides."""

    issue_date: date
    os_numbers: list[str]
    items: list[ProtocolItem] = Field(min_length=1)

    @field_validator("os_numbers", mode="before")
    @classmethod
    def clean_os_numbers(cls, value: object) -> list[str]:
        if value is None:
            return []
        raw_values = [value] if isinstance(value, (str, int)) else list(value)  # type: ignore[arg-type]
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in raw_values:
            number = normalize_os(raw)
            if number and number not in seen:
                normalized.append(number)
                seen.add(number)
        return normalized

    @model_validator(mode="after")
    def validate_business_rules(self) -> Campaign:
        if not self.os_numbers:
            raise ValueError("Informe pelo menos uma OS.")
        if not self.items:
            raise ValueError("Adicione pelo menos um tabloide.")

        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Cada tabloide precisa ter um identificador único.")
        return self

    def header_os_numbers(self) -> list[str]:
        """Retorna as OS na ordem digitada, já normalizadas e sem duplicatas."""
        return list(self.os_numbers)

    def filename_os_part(self) -> str:
        """Mantém nomes de arquivo seguros mesmo quando há muitas OS."""
        joined = "_".join(self.os_numbers)
        if len(self.os_numbers) <= 4 and len(joined) <= 80:
            return joined
        first_number = self.os_numbers[0][:30]
        return f"{first_number}_MAIS_{len(self.os_numbers) - 1}_OS"

    def quantity_for(
        self,
        store: Store,
        item: ProtocolItem,
        overrides: dict[str, dict[str, int]] | None = None,
    ) -> int:
        if overrides and store.id in overrides and item.id in overrides[store.id]:
            value = int(overrides[store.id][item.id])
            if value <= 0:
                raise ValueError(f"A quantidade de {store.name} deve ser maior que zero.")
            return value
        return item.quantity_for(store.id)

    def items_for_store(
        self,
        store_id: str,
        overrides: dict[str, dict[str, int]] | None = None,
    ) -> list[ProtocolItem]:
        """Retorna somente os tabloides aplicáveis ao endereço informado."""
        temporary_items = set((overrides or {}).get(store_id, {}))
        return [
            item for item in self.items if item.applies_to(store_id) or item.id in temporary_items
        ]
