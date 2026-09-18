"""Fatos estruturados -> ``graph.add_fact_triple`` (sem LLM, sem erro de extracao).

Tudo que ja chega estruturado no webhook (setor, atendente, tags, canal,
encerramento) entra por aqui. Restricoes: ``fact`` <= 250 caracteres,
``fact_name`` em SNAKE_CASE com 1-50 caracteres, um rotulo por no.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.zep.client import get_zep

log = logging.getLogger(__name__)

FACT_MAX = 250
_FACT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")


@dataclass
class FactTriple:
    fact: str
    fact_name: str
    source_name: str
    source_label: str
    target_name: str
    target_label: str
    valid_at: str | None = None
    invalid_at: str | None = None
    edge_attributes: dict[str, Any] = field(default_factory=dict)
    target_attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _FACT_NAME.match(self.fact_name):
            raise ValueError(f"fact_name invalido: {self.fact_name!r}")
        if len(self.fact) > FACT_MAX:
            self.fact = self.fact[: FACT_MAX - 1].rstrip() + "…"


async def add_fact(user_id: str | None, triple: FactTriple, *, graph_id: str | None = None) -> None:
    await get_zep().graph.add_fact_triple(
        user_id=user_id,
        graph_id=graph_id,
        fact=triple.fact,
        fact_name=triple.fact_name,
        source_node_name=triple.source_name,
        source_node_labels=[triple.source_label],
        target_node_name=triple.target_name,
        target_node_labels=[triple.target_label],
        target_node_attributes=triple.target_attributes or None,
        edge_attributes=triple.edge_attributes or None,
        valid_at=triple.valid_at,
        invalid_at=triple.invalid_at,
    )
    log.info("zep: fato [%s] %s", triple.fact_name, triple.fact)


# --- Fabricas dos fatos que o webhook do Talk permite afirmar -----------------


def fact_atendido_por(contact: str, member: str, valid_at: str | None, origem: str) -> FactTriple:
    return FactTriple(
        fact=f"{contact} é atendido(a) por {member} no Umbler Talk.",
        fact_name="ATENDIDO_POR",
        source_name=contact,
        source_label="User",
        target_name=member,
        target_label="Vendedor",
        valid_at=valid_at,
        edge_attributes={"origem": origem},
    )


def fact_setor(contact: str, sector: str, valid_at: str | None) -> FactTriple:
    return FactTriple(
        fact=f"{contact} está sendo atendido(a) no setor {sector} do Umbler Talk.",
        fact_name="ATENDIDO_NO_SETOR",
        source_name=contact,
        source_label="User",
        target_name=sector,
        target_label="Setor",
        valid_at=valid_at,
    )


def fact_encerrado(
    contact: str, sector: str, closed_at: str | None, closer: str | None
) -> FactTriple:
    quem = f" por {closer}" if closer else ""
    quando = f" em {closed_at[:10]}" if closed_at else ""
    return FactTriple(
        fact=f"Atendimento de {contact} no setor {sector} foi encerrado{quando}{quem}.",
        fact_name="ATENDIMENTO_ENCERRADO",
        source_name=contact,
        source_label="User",
        target_name=sector,
        target_label="Setor",
        valid_at=closed_at,
    )


def fact_tag(contact: str, tag: str, valid_at: str | None, where: str) -> FactTriple:
    return FactTriple(
        fact=f"{contact} tem a tag '{tag}' no Umbler Talk.",
        fact_name="TEM_TAG",
        source_name=contact,
        source_label="User",
        target_name=tag,
        target_label="Tag",
        valid_at=valid_at,
        edge_attributes={"aplicada_em": where},
    )


def fact_canal(
    contact: str, channel: str, identifier: str | None, valid_at: str | None
) -> FactTriple:
    via = f" ({identifier})" if identifier else ""
    return FactTriple(
        fact=f"{contact} conversa com a Umbler pelo canal {channel}{via}.",
        fact_name="CONTATO_PELO_CANAL",
        source_name=contact,
        source_label="User",
        target_name=channel,
        target_label="Canal",
        valid_at=valid_at,
        edge_attributes={"identificador": identifier or ""},
    )
