"""Ontologia do grafo: o vocabulario comercial da Umbler.

E uma *dica* para o extrator, nao um schema: a classificacao e feita por LLM
lendo a **descricao** de cada tipo. Regras (APRENDIZADOS-ZEP.md, secao 5, e
docs do Zep):

* limite por plano, separado para entidades e arestas: Free 5, Flex 10,
  Flex Plus 20. Esta ontologia tem 9 + 9, pensada para o Flex, com 1 + 1 de
  folga;
* todo tipo precisa de >= 1 propriedade; maximo 10 campos;
* nomes proibidos: uuid, name, graph_id, name_embedding, summary, created_at;
* tipos mutuamente exclusivos, descricao clara: cada fato vai para UM tipo;
* ``set_ontology`` sobrescreve tudo e **nao e retroativo** -- aplique antes do
  primeiro webhook (``python -m scripts.setup_zep``).

O tipo embutido ``User`` representa o proprio contato; por isso nao existe um
tipo "Lead" ou "Cliente" aqui.

Fora do grafo, de proposito: setor, canal, tags e estado do chat. Sao estado
operacional, volatil, e ficam no Store (expostos em ``/briefing``) e na
metadata das mensagens, que e filtravel na busca.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field
from zep_cloud import EntityEdgeSourceTarget
from zep_cloud.external_clients.ontology import EdgeModel, EntityInt, EntityModel, EntityText

from app.zep.client import get_zep

# --- Entidades ---------------------------------------------------------------


class Vendedor(EntityModel):
    """Pessoa do time da Umbler (vendas, suporte ou sucesso do cliente) que atende o contato pelo Umbler Talk, e-mail ou telefone. Diferente de User, que e o proprio contato/cliente."""

    equipe: EntityText = Field(
        default=None,
        description="Time do atendente: Vendas, Suporte, Financeiro, Sucesso do Cliente.",
    )


class EmpresaCliente(EntityModel):
    """Empresa onde o contato trabalha ou que ele representa e que usa ou avalia produtos da Umbler. Prefira este tipo a Organization para empresas de clientes e prospects."""

    segmento: EntityText = Field(
        default=None, description="Ramo de atuacao: agencia, e-commerce, clinica, imobiliaria, etc."
    )
    cidade: EntityText = Field(default=None, description="Cidade e estado da empresa.")
    porte: EntityText = Field(
        default=None, description="Tamanho aproximado: numero de funcionarios, atendentes ou sites."
    )


class ProdutoUmbler(EntityModel):
    """Produto ou servico vendido pela Umbler. O principal e o Umbler Talk (atendimento por WhatsApp); tambem hospedagem de sites, e-mail profissional, dominios e servidores. Prefira este tipo a Object ou Topic para ofertas da Umbler."""

    categoria: EntityText = Field(
        default=None,
        description="Categoria: atendimento, hospedagem, e-mail, dominio, servidor.",
    )


class Plano(EntityModel):
    """Plano comercial nomeado de um produto da Umbler, com preco e limites (por exemplo os planos do Umbler Talk). Prefira este tipo a Product para pacotes comerciais."""

    produto: EntityText = Field(default=None, description="Produto ao qual o plano pertence.")
    preco_mensal: EntityInt = Field(
        default=None, description="Preco mensal em reais, quando dito explicitamente."
    )


class Negociacao(EntityModel):
    """Oportunidade comercial nomeada, com estagio no funil e valor esperado. So existe quando a fonte nomeia uma oportunidade concreta (proposta, deal do CRM)."""

    estagio: EntityText = Field(
        default=None,
        description="Etapa do funil: qualificacao, demonstracao, proposta, fechamento, ganha, perdida ou adiada.",
    )
    valor_mensal: EntityInt = Field(default=None, description="Receita mensal esperada em reais.")
    origem: EntityText = Field(
        default=None, description="De onde veio a oportunidade: WhatsApp, e-mail, CRM, indicacao."
    )


class Necessidade(EntityModel):
    """Dor, problema ou caso de uso que o contato quer resolver com um produto da Umbler: varios atendentes no mesmo WhatsApp, nao perder leads, integrar com outro sistema, reduzir custo. Nao e uma objecao."""

    tipo: EntityText = Field(
        default=None,
        description="Natureza: atendimento, organizacao, integracao, escala, custo, controle, automacao.",
    )
    urgencia: EntityText = Field(default=None, description="Quao urgente o contato diz que e.")


class Objecao(EntityModel):
    """Duvida, resistencia ou bloqueio levantado pelo contato que trava a compra, a expansao ou a renovacao. Nao e uma necessidade."""

    tipo: EntityText = Field(
        default=None,
        description="Natureza: preco, integracao, prazo, concorrente, seguranca, suporte, tecnica.",
    )


class Concorrente(EntityModel):
    """Empresa ou ferramenta concorrente da Umbler que o contato usa hoje, ja usou ou esta comparando."""

    situacao: EntityText = Field(
        default=None, description="Se o contato usa hoje, ja usou, esta migrando ou apenas cotou."
    )


class Compromisso(EntityModel):
    """Proximo passo combinado entre contato e atendente, com prazo: enviar proposta, reuniao, decisao do socio, retorno em data. E um Event; prefira este tipo a Event para combinados comerciais."""

    prazo: EntityText = Field(default=None, description="Data ou prazo combinado.")
    responsavel: EntityText = Field(
        default=None, description="Quem deve agir: o contato, o atendente ou terceiro."
    )
    status: EntityText = Field(
        default=None, description="Pendente, cumprido, atrasado ou cancelado."
    )


# --- Arestas -----------------------------------------------------------------


class ATENDIDO_POR(EdgeModel):
    """Liga o contato ao atendente da Umbler responsavel pela conversa."""

    origem: EntityText = Field(
        default=None, description="Como foi definido: transferencia, bot ou atribuicao."
    )


class TRABALHA_EM(EdgeModel):
    """Liga o contato a empresa onde trabalha ou que representa."""

    cargo: EntityText = Field(
        default=None, description="Cargo do contato: socio, gerente, desenvolvedor, marketing."
    )


class USA(EdgeModel):
    """O contato ou sua empresa JA E CLIENTE do produto ou plano da Umbler: usa hoje ou usou. Se ainda esta decidindo, use AVALIA."""

    situacao: EntityText = Field(
        default=None, description="Ativo, em teste gratuito, cancelado ou migrado."
    )


class AVALIA(EdgeModel):
    """O contato ou sua empresa AINDA NAO COMPROU e esta considerando o produto ou plano da Umbler. Se ja usa, use USA."""

    motivo: EntityText = Field(
        default=None, description="Por que esse produto ou plano faz sentido."
    )


class TEM_NEGOCIACAO(EdgeModel):
    """Liga o contato ou a empresa a uma oportunidade comercial em andamento."""

    prioridade: EntityText = Field(default=None, description="Prioridade do negocio para o time.")


class TEM_NECESSIDADE(EdgeModel):
    """Liga o contato a uma dor ou caso de uso que ele quer resolver."""

    impacto: EntityText = Field(
        default=None, description="O que acontece hoje por causa dessa dor."
    )


class LEVANTOU_OBJECAO(EdgeModel):
    """Liga o contato a uma objecao que ele apresentou."""

    status: EntityText = Field(
        default=None, description="Se foi contornada, segue aberta ou virou bloqueio."
    )


class COMPARA_COM(EdgeModel):
    """Liga o contato ou a negociacao a um concorrente com quem a Umbler esta sendo comparada."""

    diferencial: EntityText = Field(
        default=None, description="Ponto decisivo citado na comparacao."
    )


class COMBINOU(EdgeModel):
    """Liga o contato ou o atendente a um compromisso assumido na conversa."""

    prazo: EntityText = Field(default=None, description="Data ou prazo dito na conversa.")


ENTIDADES: dict[str, type[EntityModel]] = {
    "Vendedor": Vendedor,
    "EmpresaCliente": EmpresaCliente,
    "ProdutoUmbler": ProdutoUmbler,
    "Plano": Plano,
    "Negociacao": Negociacao,
    "Necessidade": Necessidade,
    "Objecao": Objecao,
    "Concorrente": Concorrente,
    "Compromisso": Compromisso,
}

_QUEM = ("User", "EmpresaCliente")
_OFERTA = ("ProdutoUmbler", "Plano")

ARESTAS = {
    "ATENDIDO_POR": (ATENDIDO_POR, [EntityEdgeSourceTarget(source="User", target="Vendedor")]),
    "TRABALHA_EM": (TRABALHA_EM, [EntityEdgeSourceTarget(source="User", target="EmpresaCliente")]),
    "USA": (USA, [EntityEdgeSourceTarget(source=s, target=t) for s in _QUEM for t in _OFERTA]),
    "AVALIA": (
        AVALIA,
        [EntityEdgeSourceTarget(source=s, target=t) for s in _QUEM for t in _OFERTA],
    ),
    "TEM_NEGOCIACAO": (
        TEM_NEGOCIACAO,
        [EntityEdgeSourceTarget(source=s, target="Negociacao") for s in _QUEM],
    ),
    "TEM_NECESSIDADE": (
        TEM_NECESSIDADE,
        [EntityEdgeSourceTarget(source="User", target="Necessidade")],
    ),
    "LEVANTOU_OBJECAO": (
        LEVANTOU_OBJECAO,
        [EntityEdgeSourceTarget(source="User", target="Objecao")],
    ),
    "COMPARA_COM": (
        COMPARA_COM,
        [
            EntityEdgeSourceTarget(source="User", target="Concorrente"),
            EntityEdgeSourceTarget(source="Negociacao", target="Concorrente"),
        ],
    ),
    "COMBINOU": (
        COMBINOU,
        [
            EntityEdgeSourceTarget(source="User", target="Compromisso"),
            EntityEdgeSourceTarget(source="Vendedor", target="Compromisso"),
        ],
    ),
}

ENTITY_TYPES: tuple[str, ...] = tuple(ENTIDADES)
EDGE_TYPES: tuple[str, ...] = tuple(ARESTAS)

# Teto do plano Flex. Se mudar de plano, ajuste aqui e no README.
PLAN_LIMIT = 10
assert len(ENTITY_TYPES) <= PLAN_LIMIT and len(EDGE_TYPES) <= PLAN_LIMIT


@dataclass(frozen=True)
class OntologyDiff:
    missing_entity_types: tuple[str, ...]
    missing_edge_types: tuple[str, ...]
    extra_entity_types: tuple[str, ...]
    extra_edge_types: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return not (
            self.missing_entity_types
            or self.missing_edge_types
            or self.extra_entity_types
            or self.extra_edge_types
        )


async def apply_ontology() -> None:
    """Aplica ao projeto inteiro (grafos de contato e grafo da Umbler)."""
    await get_zep().graph.set_ontology(entities=ENTIDADES, edges=ARESTAS)


async def verify_ontology() -> OntologyDiff:
    live = await get_zep().graph.list_entity_types()
    live_entities = {e.name for e in (live.entity_types or [])}
    live_edges = {e.name for e in (live.edge_types or [])}
    return OntologyDiff(
        missing_entity_types=tuple(sorted(set(ENTITY_TYPES) - live_entities)),
        missing_edge_types=tuple(sorted(set(EDGE_TYPES) - live_edges)),
        extra_entity_types=tuple(sorted(live_entities - set(ENTITY_TYPES))),
        extra_edge_types=tuple(sorted(live_edges - set(EDGE_TYPES))),
    )
