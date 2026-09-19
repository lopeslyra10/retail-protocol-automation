from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppSettings:
    """Configurações de apresentação sem acoplar o domínio a uma marca específica."""

    app_title: str = "Retail Protocol Automation"
    document_brand: str = "Retail Demo"
    filename_brand: str = "RETAIL_DEMO"


def load_settings() -> AppSettings:
    return AppSettings(
        app_title=os.getenv("RPA_APP_TITLE", AppSettings.app_title),
        document_brand=os.getenv("RPA_DOCUMENT_BRAND", AppSettings.document_brand),
        filename_brand=os.getenv("RPA_FILENAME_BRAND", AppSettings.filename_brand),
    )


settings = load_settings()
