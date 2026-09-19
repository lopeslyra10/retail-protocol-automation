from __future__ import annotations

import json
from datetime import date
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from retail_protocols.models import Campaign, ProtocolItem, QuantityProfile, normalize_os
from retail_protocols.repository import QuantityProfileRepository, StoreRepository
from retail_protocols.services import (
    EmailListParser,
    ProtocolPDFGenerator,
    ProtocolZipService,
    QuantityListParser,
)

ROOT = Path(__file__).resolve().parents[1]


def store_repository() -> StoreRepository:
    return StoreRepository(ROOT / "data" / "stores.json")


def profile_repository() -> QuantityProfileRepository:
    store_ids = [store.id for store in store_repository().list_all(active_only=False)]
    return QuantityProfileRepository(
        ROOT / "data" / "quantity_profiles.json",
        valid_store_ids=store_ids,
    )


def profile(profile_id: str) -> QuantityProfile:
    return profile_repository().find_by_id(profile_id)


def protocol_item(
    item_id: str,
    description: str,
    quantity_profile: QuantityProfile,
) -> ProtocolItem:
    return ProtocolItem(
        id=item_id,
        description=description,
        quantity_profile_id=quantity_profile.id,
        quantity_profile_name=quantity_profile.name,
        quantities=quantity_profile.quantities,
    )


def three_material_campaign() -> Campaign:
    weekly = profile("campanha_alto_fluxo")
    flyer = profile("flyer_lancamento_parcial")
    return Campaign(
        issue_date=date(2026, 9, 15),
        os_numbers=["OS. 104582", "104583", "104584"],
        items=[
            protocol_item(
                "material_semanal",
                "MATERIAL PROMOCIONAL 15-09 a 21-09",
                weekly,
            ),
            protocol_item("flyer", "FLYER DE LANÇAMENTO", flyer),
            protocol_item("material_extra", "MATERIAL ESPECIAL", weekly),
        ],
    )


def pdf_page_count(content: bytes) -> int:
    return content.count(b"/Type /Page") - content.count(b"/Type /Pages")


def email_with_tables(*tables: list[tuple[str, str, int]]) -> bytes:
    message = EmailMessage()
    message["From"] = "operacao@example.com"
    message["To"] = "protocolos@example.com"
    message["Date"] = "Wed, 26 Aug 2026 13:20:24 -0300"
    message["Subject"] = "Fwd: OS 104582 - Material Promocional 27-08 a 02-09"
    message.set_content("A quantidade atual é 950 unidades. A versão anterior tinha 750 unidades.")

    html_tables = []
    for table in tables:
        rows = "".join(
            f"<tr><td>{name}</td><td>{address}</td><td>{quantity}</td></tr>"
            for name, address, quantity in table
        )
        html_tables.append(
            f"<table><tr><th>LOJAS</th><th>ENDEREÇOS</th><th>QTD TOTAL</th></tr>{rows}</table>"
        )
    message.add_alternative(
        "<html><body>" + "".join(html_tables) + "</body></html>",
        subtype="html",
    )
    return message.as_bytes()


def test_store_master_uses_only_synthetic_demo_data() -> None:
    stores = store_repository().list_all()

    assert len(stores) == 12
    assert all(store.name.startswith("Unidade ") for store in stores)
    assert all(store.default_quantity == 100 for store in stores)


def test_quantity_profiles_have_full_and_partial_examples() -> None:
    profiles = profile_repository().list_all()

    assert {profile.category for profile in profiles} == {"Campanha semanal", "Flyer"}
    assert sum(profile("campanha_semanal_padrao").quantities.values()) == 2780
    assert len(profile("flyer_lancamento_parcial").quantities) == 6


def test_os_normalization() -> None:
    assert normalize_os("OS. 104582") == "104582"
    assert normalize_os(" 104.582 ") == "104582"
    assert normalize_os("sem número") is None


def test_campaign_accepts_any_number_of_os_and_preserves_order() -> None:
    payload = three_material_campaign().model_dump()
    payload["os_numbers"] = ["OS 10", "20", "OS 10", "30"]

    campaign = Campaign.model_validate(payload)

    assert campaign.header_os_numbers() == ["10", "20", "30"]


def test_campaign_requires_at_least_one_valid_os() -> None:
    payload = three_material_campaign().model_dump()
    payload["os_numbers"] = ["sem número"]

    with pytest.raises(ValueError, match="pelo menos uma OS"):
        Campaign.model_validate(payload)


def test_campaign_rejects_duplicate_item_ids() -> None:
    payload = three_material_campaign().model_dump()
    payload["items"][1]["id"] = payload["items"][0]["id"]

    with pytest.raises(ValueError, match="identificador único"):
        Campaign.model_validate(payload)


def test_materials_keep_independent_profiles_and_overrides() -> None:
    campaign = three_material_campaign()
    store = store_repository().find_by_id("unidade_central")
    weekly, flyer, extra = campaign.items

    assert campaign.quantity_for(store, weekly) == 500
    assert campaign.quantity_for(store, flyer) == 1000
    assert campaign.quantity_for(store, extra) == 500

    overrides = {store.id: {extra.id: 999}}
    assert campaign.quantity_for(store, weekly, overrides) == 500
    assert campaign.quantity_for(store, extra, overrides) == 999


def test_campaign_filters_materials_by_store() -> None:
    campaign = three_material_campaign()

    assert [item.id for item in campaign.items_for_store("unidade_central")] == [
        "material_semanal",
        "flyer",
        "material_extra",
    ]
    assert [item.id for item in campaign.items_for_store("unidade_norte")] == [
        "material_semanal",
        "material_extra",
    ]


def test_pdf_generation_with_three_materials() -> None:
    store = store_repository().find_by_id("unidade_central")

    name, content = ProtocolPDFGenerator().generate(three_material_campaign(), store)

    assert name.endswith(".pdf")
    assert "104582_104583_104584" in name
    assert content.startswith(b"%PDF")
    assert len(content) > 5000
    assert pdf_page_count(content) == 1


def test_many_materials_are_paginated_in_one_pdf() -> None:
    weekly = profile("campanha_alto_fluxo")
    items = [
        protocol_item(
            f"material_{index:02d}",
            f"MATERIAL PROMOCIONAL {index:02d}",
            weekly,
        )
        for index in range(1, 26)
    ]
    campaign = Campaign(
        issue_date=date(2026, 9, 15),
        os_numbers=["104582"],
        items=items,
    )

    _name, content = ProtocolPDFGenerator().generate(
        campaign,
        store_repository().list_all()[0],
    )

    assert pdf_page_count(content) == 2


def test_many_os_get_index_pages_and_safe_filename() -> None:
    weekly = profile("campanha_alto_fluxo")
    campaign = Campaign(
        issue_date=date(2026, 9, 15),
        os_numbers=[str(200000 + index) for index in range(100)],
        items=[protocol_item("material", "MATERIAL PROMOCIONAL", weekly)],
    )

    name, content = ProtocolPDFGenerator().generate(
        campaign,
        store_repository().list_all()[0],
    )

    assert "MAIS_99_OS" in name
    assert len(name) < 180
    assert pdf_page_count(content) == 4


def test_pdf_rejects_store_outside_every_profile() -> None:
    stores = store_repository().list_all()
    partial = QuantityProfile(
        id="partial_pdf",
        name="Lista parcial para PDF",
        category="Especial",
        quantities={store.id: 75 for store in stores[:4]},
    )
    campaign = Campaign(
        issue_date=date(2026, 9, 15),
        os_numbers=["104582"],
        items=[protocol_item("partial", "MATERIAL PARCIAL", partial)],
    )

    with pytest.raises(ValueError, match="Nenhum tabloide selecionado"):
        ProtocolPDFGenerator().generate(campaign, stores[-1])


def test_zip_contains_only_individual_pdfs() -> None:
    stores = store_repository().list_all()[:3]

    name, content = ProtocolZipService().generate_zip(three_material_campaign(), stores)

    assert name.endswith(".zip")
    with ZipFile(BytesIO(content)) as archive:
        members = archive.namelist()
        assert len(members) == 3
        assert all(member.endswith(".pdf") for member in members)
        assert all("/" not in member for member in members)


def test_zip_with_partial_profile_generates_only_selected_units() -> None:
    stores = store_repository().list_all()
    partial = QuantityProfile(
        id="partial_zip",
        name="Lista parcial para ZIP",
        category="Especial",
        quantities={store.id: 75 for store in stores[:5]},
    )
    campaign = Campaign(
        issue_date=date(2026, 9, 15),
        os_numbers=["104582"],
        items=[protocol_item("partial", "MATERIAL PARCIAL", partial)],
    )

    _name, content = ProtocolZipService().generate_zip(campaign, stores)

    with ZipFile(BytesIO(content)) as archive:
        assert len(archive.namelist()) == 5


def test_quantity_profile_accepts_a_free_category() -> None:
    quantities = {store.id: 100 for store in store_repository().list_all()}
    current = QuantityProfile(
        id="winter_festival",
        name="Festival de inverno",
        category="Ação sazonal",
        quantities=quantities,
    )

    current.validate_store_ids(set(quantities))

    assert current.category == "Ação sazonal"


def test_quantity_profile_rejects_unknown_stores() -> None:
    current = QuantityProfile(
        id="invalid_profile",
        name="Lista inválida",
        category="Especial",
        quantities={"unidade_inexistente": 100},
    )

    with pytest.raises(ValueError, match="lojas desconhecidas"):
        current.validate_store_ids({store.id for store in store_repository().list_all()})


def test_repository_persists_a_new_profile(tmp_path: Path) -> None:
    target = tmp_path / "profiles.json"
    target.write_text("[]", encoding="utf-8")
    stores = store_repository().list_all()
    repository = QuantityProfileRepository(
        target,
        valid_store_ids=[store.id for store in stores],
    )
    current = QuantityProfile(
        id="new_profile",
        name="Nova distribuição",
        category="Especial",
        quantities={stores[0].id: 125},
    )

    repository.save(current)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload[0]["id"] == "new_profile"
    assert repository.find_by_id("new_profile").quantities == {stores[0].id: 125}


def test_quantity_parser_accepts_aliases_and_complete_list() -> None:
    stores = store_repository().list_all()
    content = "\n".join(
        f"{('Central' if store.id == 'unidade_central' else store.name)};200" for store in stores
    )

    result = QuantityListParser(stores).parse_text(content)

    assert result.is_valid
    assert len(result.quantities) == 12
    assert result.quantities["unidade_central"] == 200


def test_quantity_parser_accepts_partial_lists_when_requested() -> None:
    stores = store_repository().list_all()

    result = QuantityListParser(stores).parse_text(
        f"{stores[0].name};200\n{stores[1].name};300",
        require_all_stores=False,
    )

    assert result.is_valid
    assert result.quantities == {stores[0].id: 200, stores[1].id: 300}


def test_quantity_parser_reports_unknown_and_duplicate_names() -> None:
    stores = store_repository().list_all()

    result = QuantityListParser(stores).parse_text(
        f"{stores[0].name};100\n{stores[0].name};200\nLoja desconhecida;300",
        require_all_stores=False,
    )

    assert result.duplicate_names == [stores[0].name]
    assert result.unknown_names == ["Loja desconhecida"]
    assert not result.is_valid


def test_email_import_extracts_metadata_and_distinct_revisions() -> None:
    stores = store_repository().list_all()[:3]
    current = [
        (stores[0].name, stores[0].address, 200),
        (stores[1].name, stores[1].address, 500),
        (stores[2].name, stores[2].address, 250),
    ]
    previous = [
        (stores[0].name, stores[0].address, 200),
        (stores[1].name, stores[1].address, 300),
        (stores[2].name, stores[2].address, 250),
    ]

    result = EmailListParser(stores).parse(email_with_tables(current, current, previous))

    assert result.os_numbers == ("104582",)
    assert result.tabloid_description == "MATERIAL PROMOCIONAL 27-08 a 02-09"
    assert result.profile_name == "Material Promocional 27-08 a 02-09"
    assert result.category == "Material Promocional"
    assert result.issue_date == date(2026, 8, 27)
    assert result.declared_totals == (950, 750)
    assert len(result.tables) == 2
    assert result.tables[0].total == 950
    assert result.tables[1].total == 750
    assert result.recommended_table.is_valid


def test_email_import_accepts_partial_address_list() -> None:
    stores = store_repository().list_all()
    selected = stores[:2]
    table = [(store.name, store.address, 75) for store in selected]

    result = EmailListParser(stores).parse(email_with_tables(table))

    assert result.recommended_table.is_valid
    assert result.recommended_table.quantities == {
        selected[0].id: 75,
        selected[1].id: 75,
    }


def test_email_import_uses_address_when_store_label_is_different() -> None:
    stores = store_repository().list_all()
    table = [("Nome novo ainda sem alias", stores[0].address, 125)]

    result = EmailListParser(stores).parse(email_with_tables(table))
    row = result.recommended_table.rows[0]

    assert row.store_id == stores[0].id
    assert row.match_method == "endereço"
    assert result.recommended_table.is_valid


def test_email_import_plain_text_fallback() -> None:
    stores = store_repository().list_all()[:2]
    message = EmailMessage()
    message["Date"] = "Wed, 26 Aug 2026 13:20:24 -0300"
    message["Subject"] = "OS 104582 | Material Promocional 27-08 a 02-09"
    message.set_content(
        "\n".join(
            [
                "LOJAS",
                "ENDEREÇOS",
                "QTD TOTAL",
                stores[0].name,
                stores[0].address,
                "150",
                stores[1].name,
                stores[1].address,
                "500",
            ]
        )
    )

    result = EmailListParser(stores).parse(message.as_bytes())

    assert result.recommended_table.total == 650
    assert result.recommended_table.matched_count == 2


def test_repository_sample_email_is_importable() -> None:
    content = (ROOT / "examples" / "sample_distribution.eml").read_bytes()

    result = EmailListParser(store_repository().list_all()).parse(content)

    assert result.os_numbers == ("104582",)
    assert result.issue_date == date(2026, 9, 15)
    assert result.recommended_table.matched_count == 3
    assert result.recommended_table.total == 1050
    assert result.recommended_table.is_valid


def test_email_import_separates_named_lists_and_maps_each_os() -> None:
    message = EmailMessage()
    message["Date"] = "Thu, 10 Sep 2026 19:11:27 -0300"
    message["Subject"] = (
        "Re: OS: 104583 / 104584 - Atualização de orçamento "
        "Campanha Regional 09-09 a 15-09 e Flyer Lançamento"
    )
    message.set_content(
        "\n".join(
            [
                "Distribuição Flyer Lançamento:",
                "Nome da loja",
                "Nº flyers",
                "Unidade Central",
                "1000",
                "Unidade Jardins",
                "800",
                "Total:",
                "1800",
                "Distribuição Campanha Regional 09-09 a 15-09:",
                "Unidade Norte",
                "100",
                "Unidade Sul",
                "100",
                "Mensagem original",
                "104584 (Flyer Lançamento) já liberada para produção.",
                "Qtde: 1.800 unidades",
            ]
        )
    )

    result = EmailListParser(store_repository().list_all()).parse(message.as_bytes())
    flyer, campaign = result.tables

    assert result.os_numbers == ("104583", "104584")
    assert flyer.label == "Flyer Lançamento"
    assert flyer.os_numbers == ("104584",)
    assert flyer.profile_name == "Flyer Lançamento"
    assert flyer.category == "Flyer Lançamento"
    assert flyer.issue_date == date(2026, 9, 10)
    assert flyer.quantities == {
        "unidade_central": 1000,
        "unidade_jardins": 800,
    }
    assert flyer.is_valid

    assert campaign.label == "Campanha Regional 09-09 a 15-09"
    assert campaign.os_numbers == ("104583",)
    assert campaign.profile_name == "Campanha Regional 09-09 a 15-09"
    assert campaign.category == "Campanha Regional"
    assert campaign.issue_date == date(2026, 9, 9)
