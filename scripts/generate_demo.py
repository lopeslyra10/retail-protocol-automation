from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retail_protocols.config import settings  # noqa: E402
from retail_protocols.models import Campaign, ProtocolItem  # noqa: E402
from retail_protocols.repository import (  # noqa: E402
    QuantityProfileRepository,
    StoreRepository,
)
from retail_protocols.services import (  # noqa: E402
    ProtocolPDFGenerator,
    ProtocolZipService,
)


def main() -> None:
    stores = StoreRepository(ROOT / "data" / "stores.json").list_all()
    profiles = QuantityProfileRepository(
        ROOT / "data" / "quantity_profiles.json",
        valid_store_ids=[store.id for store in stores],
    )
    weekly = profiles.find_by_id("campanha_alto_fluxo")
    flyer = profiles.find_by_id("flyer_lancamento_parcial")

    campaign = Campaign(
        issue_date=date(2026, 9, 15),
        os_numbers=["104582", "104583"],
        items=[
            ProtocolItem(
                id="material_semanal",
                description="MATERIAL PROMOCIONAL 15-09 a 21-09",
                quantity_profile_id=weekly.id,
                quantity_profile_name=weekly.name,
                quantities=weekly.quantities,
            ),
            ProtocolItem(
                id="flyer_lancamento",
                description="FLYER DE LANÇAMENTO",
                quantity_profile_id=flyer.id,
                quantity_profile_name=flyer.name,
                quantities=flyer.quantities,
            ),
        ],
    )

    output = ROOT / "output_demo"
    output.mkdir(exist_ok=True)
    generator = ProtocolPDFGenerator(
        brand_name=settings.document_brand,
        filename_brand=settings.filename_brand,
    )
    preview_name, preview_bytes = generator.generate(campaign, stores[0])
    (output / preview_name).write_bytes(preview_bytes)

    zip_name, zip_bytes = ProtocolZipService(generator).generate_zip(campaign, stores)
    (output / zip_name).write_bytes(zip_bytes)
    print(output / preview_name)
    print(output / zip_name)


if __name__ == "__main__":
    main()
