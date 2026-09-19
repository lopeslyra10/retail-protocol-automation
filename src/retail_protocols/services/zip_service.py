from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from ..models import Campaign, Store
from .pdf_generator import ProtocolPDFGenerator


class ProtocolZipService:
    def __init__(self, generator: ProtocolPDFGenerator | None = None) -> None:
        self.generator = generator or ProtocolPDFGenerator()

    def generate_zip(
        self,
        campaign: Campaign,
        stores: list[Store],
        quantity_overrides: dict[str, dict[str, int]] | None = None,
    ) -> tuple[str, bytes]:
        if not stores:
            raise ValueError("Selecione pelo menos uma loja.")

        eligible_stores = [
            store for store in stores if campaign.items_for_store(store.id, quantity_overrides)
        ]
        if not eligible_stores:
            raise ValueError("Nenhuma loja selecionada pertence às listas dos tabloides.")

        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for sequence, store in enumerate(eligible_stores, start=1):
                filename, pdf_bytes = self.generator.generate(
                    campaign=campaign,
                    store=store,
                    sequence=sequence,
                    quantity_overrides=quantity_overrides,
                )
                archive.writestr(filename, pdf_bytes)

        os_part = campaign.filename_os_part()
        date_part = campaign.issue_date.strftime("%d-%m-%Y")
        zip_name = f"PROTOCOLOS_OS_{os_part}_{date_part}.zip"
        return zip_name, output.getvalue()
