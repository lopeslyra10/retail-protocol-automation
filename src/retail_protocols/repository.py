from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from .models import QuantityProfile, Store, normalize_category


class StoreRepository:
    def __init__(self, json_path: str | Path):
        self.json_path = Path(json_path)

    def list_all(self, active_only: bool = True) -> list[Store]:
        raw = json.loads(self.json_path.read_text(encoding="utf-8"))
        stores = [Store.model_validate(item) for item in raw]
        stores.sort(key=lambda store: store.order)
        if active_only:
            stores = [store for store in stores if store.active]
        return stores

    def find_by_id(self, store_id: str) -> Store:
        for store in self.list_all(active_only=False):
            if store.id == store_id:
                return store
        raise KeyError(f"Loja não encontrada: {store_id}")


class QuantityProfileRepository:
    _write_lock = Lock()

    def __init__(self, json_path: str | Path, valid_store_ids: Iterable[str] | None = None):
        self.json_path = Path(json_path)
        self.valid_store_ids = set(valid_store_ids) if valid_store_ids is not None else None

    def list_all(self, active_only: bool = True) -> list[QuantityProfile]:
        if not self.json_path.exists():
            return []
        raw = json.loads(self.json_path.read_text(encoding="utf-8"))
        profiles = [QuantityProfile.model_validate(item) for item in raw]
        if self.valid_store_ids is not None:
            for profile in profiles:
                profile.validate_store_ids(self.valid_store_ids)
        profiles.sort(
            key=lambda profile: (
                profile.category.casefold(),
                profile.created_at,
                profile.name.casefold(),
            )
        )
        if active_only:
            profiles = [profile for profile in profiles if profile.active]
        return profiles

    def list_by_category(
        self,
        category: str,
        active_only: bool = True,
    ) -> list[QuantityProfile]:
        expected = normalize_category(category).casefold()
        return [
            profile
            for profile in self.list_all(active_only=active_only)
            if profile.category.casefold() == expected
        ]

    def categories(self, active_only: bool = True) -> list[str]:
        categories = {profile.category for profile in self.list_all(active_only=active_only)}
        return sorted(categories, key=str.casefold)

    def find_by_id(self, profile_id: str) -> QuantityProfile:
        for profile in self.list_all(active_only=False):
            if profile.id == profile_id:
                return profile
        raise KeyError(f"Lista de quantidades não encontrada: {profile_id}")

    def save(self, profile: QuantityProfile) -> None:
        with self._write_lock:
            profiles = self.list_all(active_only=False)
            if any(current.id == profile.id for current in profiles):
                raise ValueError(f"Já existe uma lista de quantidades com o ID {profile.id}.")
            if self.valid_store_ids is not None:
                profile.validate_store_ids(self.valid_store_ids)
            profiles.append(profile)

            self.json_path.parent.mkdir(parents=True, exist_ok=True)
            payload = [item.model_dump(mode="json") for item in profiles]
            temporary_path: Path | None = None
            try:
                with NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.json_path.parent,
                    prefix=f".{self.json_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary_file:
                    json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
                    temporary_file.write("\n")
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
                    temporary_path = Path(temporary_file.name)
                temporary_path.replace(self.json_path)
            finally:
                if temporary_path and temporary_path.exists():
                    temporary_path.unlink()
