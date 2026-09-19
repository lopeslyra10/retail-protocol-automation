from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from io import StringIO

from ..models import Store


def normalize_store_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class QuantityImportResult:
    quantities: dict[str, int]
    unknown_names: list[str]
    duplicate_names: list[str]
    missing_store_ids: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.unknown_names and not self.duplicate_names and not self.missing_store_ids


class QuantityListParser:
    """Converte uma lista Loja + Quantidade para IDs oficiais de lojas."""

    def __init__(self, stores: list[Store]):
        self.stores = stores
        self.alias_map: dict[str, str] = {}
        for store in stores:
            for candidate in [store.name, *store.aliases, store.id.replace("_", " ")]:
                normalized = normalize_store_name(candidate)
                if normalized:
                    self.alias_map[normalized] = store.id

    def parse_text(self, content: str, require_all_stores: bool = True) -> QuantityImportResult:
        rows = self._read_rows(content)
        quantities: dict[str, int] = {}
        unknown_names: list[str] = []
        duplicate_names: list[str] = []

        for raw_name, raw_quantity in rows:
            normalized_name = normalize_store_name(raw_name)
            store_id = self.alias_map.get(normalized_name)
            if not store_id:
                unknown_names.append(raw_name.strip())
                continue

            if store_id in quantities:
                duplicate_names.append(raw_name.strip())
                continue

            quantity = self._parse_quantity(raw_quantity, raw_name)
            quantities[store_id] = quantity

        expected_ids = {store.id for store in self.stores}
        missing_store_ids = sorted(expected_ids - set(quantities)) if require_all_stores else []
        return QuantityImportResult(
            quantities=quantities,
            unknown_names=unknown_names,
            duplicate_names=duplicate_names,
            missing_store_ids=missing_store_ids,
        )

    @staticmethod
    def _parse_quantity(raw_quantity: str, store_name: str) -> int:
        cleaned = re.sub(r"[^0-9-]", "", raw_quantity)
        if not cleaned:
            raise ValueError(f"Quantidade inválida para {store_name}: {raw_quantity!r}.")
        quantity = int(cleaned)
        if quantity <= 0:
            raise ValueError(f"A quantidade de {store_name} deve ser maior que zero.")
        return quantity

    @staticmethod
    def _read_rows(content: str) -> list[tuple[str, str]]:
        cleaned = content.strip().lstrip("\ufeff")
        if not cleaned:
            raise ValueError("A lista importada está vazia.")

        sample = cleaned[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
            reader = csv.reader(StringIO(cleaned), dialect)
        except csv.Error:
            # Fallback para linhas do tipo "Loja 200".
            reader = csv.reader(StringIO(cleaned), delimiter=";")

        rows: list[tuple[str, str]] = []
        for line_number, row in enumerate(reader, start=1):
            row = [cell.strip() for cell in row if cell.strip()]
            if not row:
                continue

            if len(row) >= 2:
                name, quantity = row[0], row[-1]
            else:
                match = re.match(r"^(.*?)[\s]+([0-9]+)\s*$", row[0])
                if not match:
                    raise ValueError(
                        f"Linha {line_number} inválida. Use o formato Loja;Quantidade."
                    )
                name, quantity = match.group(1), match.group(2)

            # Ignora cabeçalhos comuns.
            normalized_header = normalize_store_name(name)
            if line_number == 1 and normalized_header in {
                "loja",
                "lojas",
                "nome",
                "estabelecimento",
            }:
                continue
            if not re.search(r"\d", quantity):
                if line_number == 1:
                    continue
                raise ValueError(f"Quantidade inválida na linha {line_number}: {quantity!r}.")
            rows.append((name, quantity))

        if not rows:
            raise ValueError("Nenhuma loja válida foi encontrada na lista.")
        return rows
