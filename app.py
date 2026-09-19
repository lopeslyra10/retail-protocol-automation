from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from retail_protocols.config import settings  # noqa: E402
from retail_protocols.models import (  # noqa: E402
    Campaign,
    ProtocolItem,
    QuantityProfile,
    Store,
)
from retail_protocols.repository import (  # noqa: E402
    QuantityProfileRepository,
    StoreRepository,
)
from retail_protocols.services import (  # noqa: E402
    EmailListParser,
    ProtocolPDFGenerator,
    ProtocolZipService,
)

st.set_page_config(page_title=settings.app_title, page_icon="📄", layout="wide")


@st.cache_resource
def services() -> tuple[
    StoreRepository,
    QuantityProfileRepository,
    ProtocolPDFGenerator,
    ProtocolZipService,
]:
    store_repository = StoreRepository(ROOT / "data" / "stores.json")
    valid_store_ids = [store.id for store in store_repository.list_all(active_only=False)]
    profile_repository = QuantityProfileRepository(
        ROOT / "data" / "quantity_profiles.json",
        valid_store_ids=valid_store_ids,
    )
    generator = ProtocolPDFGenerator(
        brand_name=settings.document_brand,
        filename_brand=settings.filename_brand,
    )
    return store_repository, profile_repository, generator, ProtocolZipService(generator)


def fingerprint(payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return normalized or "lista"


def row_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def category_label(value: str) -> str:
    return value


def format_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def optional_quantity(value: object) -> int | None:
    """Converte uma célula numérica; células vazias representam endereço ausente."""
    if value is None:
        return None
    try:
        if math.isnan(float(value)):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def base_quantity_for_store(profile: QuantityProfile, store: Store) -> int:
    """Sugere uma quantidade ao incluir uma loja que não existia na lista-base."""
    return profile.quantities.get(store.id, store.default_quantity)


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        messages = []
        for error in exc.errors():
            message = str(error["msg"]).removeprefix("Value error, ")
            if message not in messages:
                messages.append(message)
        return " ".join(messages)
    return str(exc)


def initialize_dynamic_form(profiles: list[QuantityProfile]) -> None:
    if "protocol_os_rows" not in st.session_state:
        st.session_state["protocol_os_rows"] = [
            {"id": row_id("os"), "value": ""},
        ]

    if "protocol_tabloid_rows" not in st.session_state:
        default_profile = profiles[0]
        st.session_state["protocol_tabloid_rows"] = [
            {
                "id": row_id("tab"),
                "description": "MATERIAL PROMOCIONAL ",
                "profile_id": default_profile.id,
            }
        ]


def render_os_fields() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = st.session_state["protocol_os_rows"]
    title_col, add_col = st.columns([6, 1.4])
    title_col.subheader("Ordens de serviço (OS)")
    add_clicked = add_col.button(
        "+ Adicionar OS",
        width="stretch",
        key="add_os_row",
    )

    action: tuple[str, int] | None = None
    for index, row in enumerate(rows):
        input_col, remove_col = st.columns([8, 0.7])
        row["value"] = input_col.text_input(
            f"OS {index + 1}",
            value=row["value"],
            placeholder="Ex.: 104582",
            key=f"os_value_{row['id']}",
        )
        if remove_col.button(
            "✕",
            key=f"remove_os_{row['id']}",
            help="Remover esta OS",
            disabled=len(rows) == 1,
            width="stretch",
        ):
            action = ("remove", index)

    if action:
        rows.pop(action[1])
        st.rerun()
    if add_clicked:
        rows.append({"id": row_id("os"), "value": ""})
        st.rerun()
    return rows


def render_tabloid_fields(
    profiles: list[QuantityProfile],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = st.session_state["protocol_tabloid_rows"]
    profiles_by_id = {profile.id: profile for profile in profiles}
    profile_ids = list(profiles_by_id)

    title_col, add_col = st.columns([6, 1.4])
    title_col.subheader("Tabloides e listas de quantidades")
    add_clicked = add_col.button(
        "+ Adicionar tabloide",
        width="stretch",
        key="add_tabloid_row",
    )
    st.caption(
        "Cada tabloide tem sua própria descrição e pode usar qualquer lista salva. "
        "Repita uma lista em vários tabloides se precisar."
    )

    action: tuple[str, int] | None = None
    for index, row in enumerate(rows):
        if row.get("profile_id") not in profiles_by_id:
            row["profile_id"] = profile_ids[0]

        with st.container(border=True):
            st.markdown(f"**Tabloide {index + 1}**")
            description_col, profile_col, up_col, down_col, remove_col = st.columns(
                [5.2, 3.2, 0.55, 0.55, 0.55]
            )
            row["description"] = description_col.text_input(
                f"Descrição do tabloide {index + 1}",
                value=row["description"],
                placeholder="Ex.: TABLOIDE MEGA PROMO 23-07 a 29-07",
                key=f"tabloid_description_{row['id']}",
            )
            current_index = profile_ids.index(row["profile_id"])
            selected_profile_id = profile_col.selectbox(
                f"Lista do tabloide {index + 1}",
                options=profile_ids,
                index=current_index,
                format_func=lambda profile_id: (
                    f"{profiles_by_id[profile_id].name} "
                    f"({category_label(profiles_by_id[profile_id].category)})"
                ),
                key=f"tabloid_profile_{row['id']}",
            )
            row["profile_id"] = selected_profile_id
            selected_profile = profiles_by_id[selected_profile_id]

            if up_col.button(
                "↑",
                key=f"move_up_{row['id']}",
                help="Mover para cima",
                disabled=index == 0,
                width="stretch",
            ):
                action = ("up", index)
            if down_col.button(
                "↓",
                key=f"move_down_{row['id']}",
                help="Mover para baixo",
                disabled=index == len(rows) - 1,
                width="stretch",
            ):
                action = ("down", index)
            if remove_col.button(
                "✕",
                key=f"remove_tabloid_{row['id']}",
                help="Remover este tabloide",
                disabled=len(rows) == 1,
                width="stretch",
            ):
                action = ("remove", index)

            st.caption(
                f"Lista: {selected_profile.name} · "
                f"{len(selected_profile.quantities)} endereço(s) · "
                f"{format_number(sum(selected_profile.quantities.values()))} unidades no total"
            )

    if action:
        operation, index = action
        if operation == "remove":
            rows.pop(index)
        elif operation == "up":
            rows[index - 1], rows[index] = rows[index], rows[index - 1]
        elif operation == "down":
            rows[index], rows[index + 1] = rows[index + 1], rows[index]
        st.rerun()

    if add_clicked:
        default_profile_id = rows[-1]["profile_id"] if rows else profile_ids[0]
        rows.append(
            {
                "id": row_id("tab"),
                "description": "TABLOIDE ",
                "profile_id": default_profile_id,
            }
        )
        st.rerun()
    return rows


def build_campaign(
    issue_date: date,
    os_rows: list[dict[str, str]],
    tabloid_rows: list[dict[str, str]],
    profiles_by_id: dict[str, QuantityProfile],
) -> Campaign:
    items: list[ProtocolItem] = []
    for row in tabloid_rows:
        profile = profiles_by_id[row["profile_id"]]
        items.append(
            ProtocolItem(
                id=row["id"],
                description=row["description"],
                quantity_profile_id=profile.id,
                quantity_profile_name=profile.name,
                quantities=profile.quantities,
            )
        )
    return Campaign(
        issue_date=issue_date,
        os_numbers=[row["value"] for row in os_rows],
        items=items,
    )


def render_generation_page() -> None:
    st.title(settings.app_title)
    st.caption(
        "Adicione quantas OS, tabloides e listas forem necessárias. "
        "O PDF organiza os itens e cria páginas adicionais automaticamente."
    )

    generation_message = st.session_state.pop("generation_prefill_message", None)
    if generation_message:
        st.success(generation_message)

    profiles = profile_repository.list_all()
    if not profiles:
        st.error("Cadastre pelo menos uma lista de quantidades antes de gerar protocolos.")
        return

    initialize_dynamic_form(profiles)
    profiles_by_id = {profile.id: profile for profile in profiles}

    pending_issue_date = st.session_state.pop("pending_protocol_issue_date", None)
    if pending_issue_date is not None:
        st.session_state["protocol_issue_date"] = pending_issue_date
    if "protocol_issue_date" not in st.session_state:
        st.session_state["protocol_issue_date"] = date.today()
    issue_date = st.date_input(
        "Data do protocolo",
        format="DD/MM/YYYY",
        key="protocol_issue_date",
    )
    os_rows = render_os_fields()
    st.divider()
    tabloid_rows = render_tabloid_fields(profiles)

    st.subheader("Lojas e quantidades")
    st.caption(
        "A tabela reúne somente os endereços presentes em pelo menos uma das listas escolhidas. "
        "Uma célula vazia indica que aquele tabloide não atende a loja."
    )

    quantity_keys = {row["id"]: f"q_{row['id']}" for row in tabloid_rows}
    applicable_store_ids = {
        store_id
        for row in tabloid_rows
        for store_id in profiles_by_id[row["profile_id"]].quantities
    }
    available_stores = [store for store in stores if store.id in applicable_store_ids]

    table_rows = []
    for store in available_stores:
        table_row: dict[str, object] = {
            "Gerar": True,
            "ID": store.id,
            "Loja": store.name,
            "Endereço": store.address,
        }
        for row in tabloid_rows:
            profile = profiles_by_id[row["profile_id"]]
            table_row[quantity_keys[row["id"]]] = profile.quantities.get(store.id)
        table_rows.append(table_row)

    column_config: dict[str, object] = {
        "Gerar": st.column_config.CheckboxColumn("Gerar", default=True),
        "ID": None,
    }
    for index, row in enumerate(tabloid_rows):
        profile = profiles_by_id[row["profile_id"]]
        column_config[quantity_keys[row["id"]]] = st.column_config.NumberColumn(
            f"{index + 1:02d} · {profile.name}",
            min_value=1,
            step=1,
        )

    editor_identity = fingerprint(
        {"items": [{"id": row["id"], "profile_id": row["profile_id"]} for row in tabloid_rows]}
    )[:16]
    edited_rows = st.data_editor(
        table_rows,
        hide_index=True,
        width="stretch",
        disabled=["ID", "Loja", "Endereço"],
        column_config=column_config,
        key=f"stores_editor_{editor_identity}",
    )

    selected_ids: list[str] = []
    overrides: dict[str, dict[str, int]] = {}
    for edited_row in edited_rows:
        if edited_row.get("Gerar"):
            store_id = str(edited_row["ID"])
            selected_ids.append(store_id)
            overrides[store_id] = {}
            for row in tabloid_rows:
                quantity = optional_quantity(edited_row[quantity_keys[row["id"]]])
                if quantity is not None:
                    overrides[store_id][row["id"]] = quantity

    selected_stores = [store for store in stores if store.id in selected_ids]
    st.metric("Lojas selecionadas", len(selected_stores))
    totals = [
        {
            "Tabloide": index + 1,
            "Descrição": row["description"].strip(),
            "Lista": profiles_by_id[row["profile_id"]].name,
            "Endereços": sum(
                1
                for store in selected_stores
                if row["id"] in overrides.get(store.id, {})
                or store.id in profiles_by_id[row["profile_id"]].quantities
            ),
            "Total": sum(
                overrides.get(store.id, {}).get(
                    row["id"],
                    profiles_by_id[row["profile_id"]].quantities.get(store.id, 0),
                )
                for store in selected_stores
            ),
        }
        for index, row in enumerate(tabloid_rows)
    ]
    st.dataframe(totals, hide_index=True, width="stretch")

    try:
        campaign = build_campaign(
            issue_date=issue_date,
            os_rows=os_rows,
            tabloid_rows=tabloid_rows,
            profiles_by_id=profiles_by_id,
        )
        if not selected_stores:
            raise ValueError("Selecione pelo menos uma loja.")
        validation_error = None
    except Exception as exc:
        campaign = None
        validation_error = friendly_error(exc)

    payload = {
        "campaign": campaign.model_dump(mode="json") if campaign else None,
        "stores": selected_ids,
        "overrides": overrides,
    }
    current_fingerprint = fingerprint(payload)

    st.divider()
    preview_col, generate_col = st.columns(2)
    with preview_col:
        if st.button(
            "Gerar prévia da primeira loja",
            type="primary",
            disabled=validation_error is not None,
        ):
            assert campaign is not None
            first_store = selected_stores[0]
            preview_name, preview_bytes = pdf_generator.generate(
                campaign=campaign,
                store=first_store,
                sequence=1,
                quantity_overrides=overrides,
            )
            st.session_state["preview_name"] = preview_name
            st.session_state["preview_bytes"] = preview_bytes
            st.session_state["preview_fingerprint"] = current_fingerprint
            st.session_state["approved"] = False

        if validation_error:
            st.warning(validation_error)
        elif st.session_state.get("preview_bytes"):
            if st.session_state.get("preview_fingerprint") != current_fingerprint:
                st.warning("Os dados mudaram depois da prévia. Gere uma nova prévia antes do ZIP.")
            else:
                st.success(f"Prévia gerada para: {selected_stores[0].name}")
                st.download_button(
                    "Baixar prévia em PDF",
                    data=st.session_state["preview_bytes"],
                    file_name=st.session_state["preview_name"],
                    mime="application/pdf",
                )
                st.session_state["approved"] = st.checkbox(
                    "Conferi e aprovo a prévia",
                    value=st.session_state.get("approved", False),
                )

    with generate_col:
        preview_is_current = st.session_state.get("preview_fingerprint") == current_fingerprint
        approved = bool(st.session_state.get("approved")) and preview_is_current
        if st.button("Gerar ZIP com os protocolos", disabled=not approved):
            assert campaign is not None
            zip_name, zip_bytes = zip_service.generate_zip(campaign, selected_stores, overrides)
            st.session_state["zip_name"] = zip_name
            st.session_state["zip_bytes"] = zip_bytes
            st.session_state["zip_fingerprint"] = current_fingerprint

        if (
            st.session_state.get("zip_bytes")
            and st.session_state.get("zip_fingerprint") == current_fingerprint
        ):
            st.success(f"ZIP pronto com {len(selected_stores)} PDF(s).")
            st.download_button(
                "Baixar protocolos em ZIP",
                data=st.session_state["zip_bytes"],
                file_name=st.session_state["zip_name"],
                mime="application/zip",
            )


def render_profiles_page() -> None:
    st.title("Listas de quantidades")
    st.caption(
        "Crie quantas listas quiser sem apagar as anteriores. "
        "Qualquer lista pode ser escolhida por qualquer tabloide."
    )

    saved_message = st.session_state.pop("profile_saved_message", None)
    if saved_message:
        st.success(saved_message)

    profiles = profile_repository.list_all()
    if profiles:
        st.subheader("Listas disponíveis")
        overview = [
            {
                "Categoria": category_label(profile.category),
                "Nome": profile.name,
                "Endereços": len(profile.quantities),
                "Total": sum(profile.quantities.values()),
                "Criada em": profile.created_at.strftime("%d/%m/%Y %H:%M"),
                "Descrição": profile.description,
            }
            for profile in profiles
        ]
        st.dataframe(overview, hide_index=True, width="stretch")
    else:
        st.warning("Ainda não há listas salvas.")
        return

    st.divider()
    with st.container(border=True):
        st.subheader("Importar lista de um e-mail")
        st.caption(
            "Envie o arquivo `.eml` recebido. O app identifica OS, tabloide, data, "
            "endereços e quantidades e deixa tudo disponível para conferência antes de salvar."
        )
        uploaded_email = st.file_uploader(
            "E-mail com a distribuição",
            type=["eml"],
            key="quantity_email_import",
            help="No Gmail, abra a mensagem, use Mais > Fazer download da mensagem e envie o arquivo .eml.",
        )

        if uploaded_email is not None:
            email_bytes = uploaded_email.getvalue()
            email_digest = hashlib.sha256(email_bytes).hexdigest()[:16]
            try:
                email_result = EmailListParser(stores).parse(email_bytes)
            except Exception as exc:
                st.error(f"Não foi possível interpretar o e-mail: {friendly_error(exc)}")
            else:
                matching_profiles_by_table = {
                    index: [
                        profile for profile in profiles if profile.quantities == table.quantities
                    ]
                    for index, table in enumerate(email_result.tables)
                }
                new_table_indexes = [
                    index
                    for index in range(len(email_result.tables))
                    if not matching_profiles_by_table[index]
                ]
                table_indexes = new_table_indexes or list(range(len(email_result.tables)))
                all_tables_are_known = not new_table_indexes

                ignored_indexes = [
                    index for index in range(len(email_result.tables)) if index not in table_indexes
                ]
                if ignored_indexes:
                    ignored_labels = [
                        email_result.tables[index].profile_name
                        or matching_profiles_by_table[index][0].name
                        for index in ignored_indexes
                    ]
                    st.info(
                        "Distribuição já cadastrada e ignorada: " + ", ".join(ignored_labels) + "."
                    )
                elif all_tables_are_known:
                    st.info("Todas as distribuições encontradas já estão cadastradas.")

                def table_label(index: int) -> str:
                    table = email_result.tables[index]
                    if table.label:
                        prefix = (
                            "Já cadastrada · " if matching_profiles_by_table[index] else "Nova · "
                        )
                        identity = f"{table.profile_name or table.label} · "
                    else:
                        prefix = "Recomendada · " if index == 0 else "Versão anterior · "
                        identity = ""
                    return (
                        f"{prefix}{identity}{table.matched_count} endereço(s) · "
                        f"{format_number(table.total)} unidades"
                    )

                selected_table_index = st.selectbox(
                    "Lista nova encontrada no e-mail",
                    options=table_indexes,
                    format_func=table_label,
                    key=f"email_table_version_{email_digest}",
                )
                selected_table = email_result.tables[selected_table_index]
                selected_os_numbers = selected_table.os_numbers or email_result.os_numbers
                selected_profile_name = selected_table.profile_name or email_result.profile_name
                selected_description = (
                    selected_table.description or email_result.tabloid_description
                )
                selected_category = selected_table.category or email_result.category
                selected_issue_date = selected_table.issue_date or email_result.issue_date

                os_col, tabloid_col, date_col, total_col = st.columns([1.1, 3.2, 1.2, 1.3])
                os_col.metric("OS", " / ".join(selected_os_numbers) or "Não identificada")
                tabloid_col.metric("Material", selected_profile_name or "Não identificado")
                date_col.metric(
                    "Data sugerida",
                    selected_issue_date.strftime("%d/%m/%Y")
                    if selected_issue_date
                    else "Não identificada",
                )
                total_col.metric("Total", format_number(selected_table.total))

                for warning in email_result.warnings:
                    st.warning(warning)

                if selected_table.total in email_result.declared_totals:
                    st.success(
                        "O total somado da tabela confere com uma quantidade total citada no e-mail."
                    )
                elif email_result.declared_totals:
                    stated = ", ".join(
                        format_number(value) for value in email_result.declared_totals
                    )
                    st.warning(
                        f"A tabela soma {format_number(selected_table.total)}, mas o texto cita: {stated}."
                    )

                review_rows = [
                    {
                        "Status": "Reconhecida" if row.is_matched else "Revisar",
                        "Loja no e-mail": row.raw_name,
                        "Loja oficial": row.official_name or "Não encontrada",
                        "Endereço no e-mail": row.raw_address,
                        "Quantidade": row.quantity,
                    }
                    for row in selected_table.rows
                ]
                st.dataframe(review_rows, hide_index=True, width="stretch")

                if selected_table.unmatched_rows:
                    unknown_names = ", ".join(row.raw_name for row in selected_table.unmatched_rows)
                    st.error(f"Lojas não reconhecidas: {unknown_names}.")
                if selected_table.duplicate_store_ids:
                    st.error(
                        "Há lojas repetidas na tabela: "
                        + ", ".join(selected_table.duplicate_store_ids)
                        + "."
                    )

                if st.button(
                    "Usar esta distribuição no cadastro",
                    type="primary",
                    disabled=(
                        not selected_table.is_valid
                        or bool(matching_profiles_by_table[selected_table_index])
                    ),
                    key=f"apply_email_table_{email_digest}_{selected_table_index}",
                ):
                    st.session_state["profile_import_draft"] = {
                        "token": f"{email_digest}_{selected_table_index}",
                        "source_file": uploaded_email.name,
                        "os_numbers": list(selected_os_numbers),
                        "tabloid_description": selected_description,
                        "profile_name": selected_profile_name,
                        "category": selected_category,
                        "issue_date": (
                            selected_issue_date.isoformat() if selected_issue_date else None
                        ),
                        "quantities": selected_table.quantities,
                        "table_total": selected_table.total,
                        "table_position": selected_table.source_position,
                    }
                    st.rerun()

    st.divider()
    st.subheader("+ Cadastrar nova lista")

    import_draft = st.session_state.get("profile_import_draft")
    if import_draft:
        st.success(
            "A distribuição do e-mail foi carregada abaixo. "
            "Confira os campos e as quantidades antes de salvar."
        )
        if st.button("Descartar dados importados", key="discard_profile_import"):
            st.session_state.pop("profile_import_draft", None)
            st.rerun()

    profiles_by_id = {profile.id: profile for profile in profiles}
    profile_ids = list(profiles_by_id)
    import_token = import_draft["token"] if import_draft else "manual"
    preferred_base_index = 0
    if import_draft:
        matching_profile_indexes = [
            index
            for index, profile_id in enumerate(profile_ids)
            if profiles_by_id[profile_id].category.casefold()
            == str(import_draft["category"]).casefold()
        ]
        if matching_profile_indexes:
            preferred_base_index = matching_profile_indexes[-1]
    base_id = st.selectbox(
        "Usar como base nos endereços não importados",
        options=profile_ids,
        index=preferred_base_index,
        format_func=lambda profile_id: (
            f"{profiles_by_id[profile_id].name} "
            f"({category_label(profiles_by_id[profile_id].category)})"
        ),
        key=f"profile_base_{import_token}",
    )
    base_profile = profiles_by_id[base_id]

    categories = profile_repository.categories()
    new_category_option = "+ Nova categoria"
    imported_category = str(import_draft["category"]) if import_draft else base_profile.category
    existing_imported_category = next(
        (value for value in categories if value.casefold() == imported_category.casefold()),
        None,
    )
    default_category_option = existing_imported_category or new_category_option
    category_options = [*categories, new_category_option]
    category_choice = st.selectbox(
        "Categoria da nova lista",
        options=category_options,
        index=category_options.index(default_category_option),
        format_func=lambda value: value if value == new_category_option else category_label(value),
        key=f"profile_category_choice_{import_token}",
    )
    if category_choice == new_category_option:
        category = st.text_input(
            "Nome da nova categoria",
            value=imported_category if import_draft else "",
            placeholder="Ex.: Festival de Inverno",
            key=f"profile_new_category_{import_token}",
        )
    else:
        category = category_choice

    name = st.text_input(
        "Nome da nova lista",
        value=str(import_draft["profile_name"]) if import_draft else "",
        placeholder="Ex.: Campanha semanal — 05-08-2026",
        key=f"profile_name_{import_token}",
    )
    description = st.text_input(
        "Observação",
        value=(
            f"Importada de {import_draft['source_file']}; "
            f"{len(import_draft['quantities'])} endereços e "
            f"{format_number(int(import_draft['table_total']))} unidades."
            if import_draft
            else ""
        ),
        placeholder="Ex.: Quantidades recebidas na planilha da campanha.",
        key=f"profile_description_{import_token}",
    )

    st.caption(
        "Marque somente os endereços que fazem parte da nova lista. "
        "As lojas desmarcadas não receberão esse tabloide."
    )

    imported_quantities = import_draft["quantities"] if import_draft else None
    profile_rows = []
    for store in stores:
        imported_quantity = (
            int(imported_quantities[store.id])
            if imported_quantities and store.id in imported_quantities
            else None
        )
        profile_rows.append(
            {
                "Incluir": (
                    store.id in imported_quantities
                    if imported_quantities is not None
                    else store.id in base_profile.quantities
                ),
                "ID": store.id,
                "Loja": store.name,
                "Endereço": store.address,
                "Quantidade": (
                    imported_quantity
                    if imported_quantity is not None
                    else base_quantity_for_store(base_profile, store)
                ),
            }
        )
    edited_profile_rows = st.data_editor(
        profile_rows,
        hide_index=True,
        width="stretch",
        disabled=["ID", "Loja", "Endereço"],
        column_config={
            "Incluir": st.column_config.CheckboxColumn("Incluir", default=False),
            "ID": None,
            "Quantidade": st.column_config.NumberColumn("Quantidade", min_value=1, step=1),
        },
        key=f"profile_editor_{base_profile.id}_{import_token}",
    )

    new_quantities = {
        str(edited_row["ID"]): int(edited_row["Quantidade"])
        for edited_row in edited_profile_rows
        if edited_row.get("Incluir")
    }
    address_metric, total_metric = st.columns(2)
    address_metric.metric("Endereços selecionados", len(new_quantities))
    total_metric.metric("Total da nova lista", format_number(sum(new_quantities.values())))

    if import_draft:
        save_col, prepare_col = st.columns(2)
        save_clicked = save_col.button(
            "Salvar somente a lista",
            width="stretch",
            key=f"save_imported_profile_{import_token}",
        )
        prepare_clicked = prepare_col.button(
            "Salvar lista e preparar protocolo",
            type="primary",
            width="stretch",
            disabled=not bool(import_draft.get("os_numbers")),
            help="Preenche automaticamente a OS, o tabloide e a data na aba Gerar protocolos.",
            key=f"save_and_prepare_{import_token}",
        )
    else:
        save_clicked = st.button("Salvar nova lista", type="primary")
        prepare_clicked = False

    if save_clicked or prepare_clicked:
        if len(name.strip()) < 3:
            st.error("Informe um nome para a nova lista.")
        elif len(category.strip()) < 2:
            st.error("Informe uma categoria para a nova lista.")
        elif not new_quantities:
            st.error("Selecione pelo menos um endereço para a nova lista.")
        else:
            profile_id = (
                f"{slugify(category)[:20]}_{slugify(name)[:35]}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            profile = QuantityProfile(
                id=profile_id,
                name=name,
                category=category,
                quantities=new_quantities,
                description=description,
            )
            profile_repository.save(profile)
            if prepare_clicked and import_draft:
                st.session_state["protocol_os_rows"] = [
                    {"id": row_id("os"), "value": number} for number in import_draft["os_numbers"]
                ]
                st.session_state["protocol_tabloid_rows"] = [
                    {
                        "id": row_id("tab"),
                        "description": import_draft["tabloid_description"],
                        "profile_id": profile.id,
                    }
                ]
                if import_draft.get("issue_date"):
                    st.session_state["pending_protocol_issue_date"] = date.fromisoformat(
                        import_draft["issue_date"]
                    )
                st.session_state["generation_prefill_message"] = (
                    "Lista salva e protocolo preenchido com os dados do e-mail. "
                    "Confira a data, gere a prévia e aprove antes do ZIP."
                )
            else:
                st.session_state["profile_saved_message"] = (
                    "Nova lista salva sem alterar as listas anteriores. "
                    "Ela já está disponível nos seletores de tabloide."
                )
            st.session_state.pop("profile_import_draft", None)
            st.rerun()

    st.warning(
        "Em execução local, as listas ficam no arquivo data/quantity_profiles.json. "
        "Em uma hospedagem gratuita, alterações em arquivo podem ser perdidas quando o servidor reiniciar."
    )


store_repository, profile_repository, pdf_generator, zip_service = services()
stores = store_repository.list_all()

page_generate, page_profiles = st.tabs(["Gerar protocolos", "Listas de quantidades"])
with page_generate:
    render_generation_page()
with page_profiles:
    render_profiles_page()
