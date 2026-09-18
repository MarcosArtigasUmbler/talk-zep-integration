"""Ontologia do grafo: o vocabulario comercial da Umbler.

E uma *dica* para o extrator, nao um schema: a classificacao e feita por LLM
lendo a **descricao** de cada tipo. Regras que custam caro se ignoradas
(APRENDIZADOS-ZEP.md, secao 5):

* todo tipo precisa de >= 1 propriedade; maximo 10 campos;
* nomes proibidos: uuid, name, graph_id, name_embedding, summary, created_at;
* ``set_ontology`` sobrescreve tudo e **nao e retroativo** -- aplique antes do
  primeiro webhook (``python -m scripts.setup_zep``).

O tipo embutido ``User`` representa o proprio contato; por isso nao existe um
tipo "Lead" ou "Cliente" aqui.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field
from zep_cloud import EntityEdgeSourceTarget
from zep_cloud.external_clients.ontology import EdgeModel, EntityInt, EntityModel, EntityText

from app.zep.client import get_zep

# --- Entidades ---------------------------------------------------------------


class Vendedor(EntityModel):
    """Pessoa do time da Umbler (vendas, suporte ou sucesso do cliente) que atende o contato. Diferente de User, que e o proprio contato/cliente."""

    equipe: EntityText = Field(
        default=None, description="Time ou setor: Vendas, Suporte, Financeiro, Sucesso do Cliente."
    )


class EmpresaCliente(EntityModel):
    """Empresa onde o contato trabalha ou que ele representa e que usa ou avalia produtos da Umbler. Prefira este tipo a Organization para empresas de clientes e prospects."""

    segmento: EntityText = Field(
        default=None,
        description="Ramo de atuacao, como agencia, e-commerce, clinica ou imobiliaria.",
    )
    cidade: EntityText = Field(default=None, description="Cidade e estado da empresa.")
    porte: EntityText = Field(
        default=None, description="Tamanho aproximado: numero de funcionarios, atendentes ou sites."
    )


class ProdutoUmbler(EntityModel):
    """Produto ou servico vendido pela Umbler: Umbler Talk, hospedagem de sites, e-mail profissional, dominios, servidores e afins. Prefira este tipo a Object ou Topic para ofertas da Umbler."""

    categoria: EntityText = Field(
        default=None,
        description="Categoria do produto: atendimento, hospedagem, e-mail, dominio, servidor.",
    )


class Plano(EntityModel):
    """Plano comercial de um produto da Umbler, com nome, preco e limites. Prefira este tipo a Product para pacotes comerciais."""

    produto: EntityText = Field(default=None, description="Produto ao qual o plano pertence.")
    preco_mensal: EntityInt = Field(
        default=None, description="Preco mensal em reais, quando dito explicitamente."
    )


class Negociacao(EntityModel):
    """Oportunidade comercial nomeada, com estagio no funil e valor esperado. So existe se a conversa nomeia uma oportunidade concreta."""

    estagio: EntityText = Field(
        default=None,
        description="Etapa do funil: qualificacao, demonstracao, proposta, fechamento, perdida ou adiada.",
    )
    valor_mensal: EntityInt = Field(default=None, description="Receita mensal esperada em reais.")


class Objecao(EntityModel):
    """Duvida, resistencia ou bloqueio levantado pelo contato que trava a compra ou a renovacao."""

    tipo: EntityText = Field(
        default=None,
        description="Natureza: preco, integracao, prazo, concorrente, seguranca, suporte ou tecnica.",
    )


class Concorrente(EntityModel):
    """Empresa ou ferramenta concorrente da Umbler que o contato usa hoje, ja usou ou esta comparando."""

    situacao: EntityText = Field(
        default=None, description="Se o contato usa hoje, ja usou, esta migrando ou apenas cotou."
    )


class Setor(EntityModel):
    """Setor (fila) de atendimento do Umbler Talk por onde o contato passou, como Vendas, Suporte ou Financeiro."""

    tipo: EntityText = Field(
        default=None, description="Funcao do setor: comercial, suporte, financeiro ou outro."
    )


class Tag(EntityModel):
    """Etiqueta aplicada ao contato ou a conversa no Umbler Talk pela equipe, como estagio do funil ou origem do lead."""

    categoria: EntityText = Field(
        default=None,
        description="O que a tag indica: estagio, origem, prioridade, produto ou outro.",
    )


class Canal(EntityModel):
    """Canal de contato do Umbler Talk pelo qual a conversa acontece: numero de WhatsApp, Instagram ou widget do site."""

    tipo: EntityText = Field(
        default=None, description="WhatsApp, Instagram, widget do site ou outro."
    )


# --- Arestas -----------------------------------------------------------------


class ATENDIDO_POR(EdgeModel):
    """Liga o contato ao atendente da Umbler responsavel pela conversa."""

    origem: EntityText = Field(
        default=None, description="Como foi definido: transferencia, bot ou atribuicao automatica."
    )


class ATENDIDO_NO_SETOR(EdgeModel):
    """Liga o contato ao setor do Umbler Talk que cuida da conversa."""

    motivo: EntityText = Field(default=None, description="Por que a conversa esta neste setor.")


class ATENDIMENTO_ENCERRADO(EdgeModel):
    """Registra que uma conversa do contato foi encerrada em um setor."""

    resultado: EntityText = Field(
        default=None, description="Desfecho do atendimento, quando conhecido."
    )


class TEM_TAG(EdgeModel):
    """Liga o contato a uma tag aplicada pela equipe no Umbler Talk."""

    aplicada_em: EntityText = Field(
        default=None, description="Se a tag foi aplicada ao contato ou a uma conversa."
    )


class CONTATO_PELO_CANAL(EdgeModel):
    """Liga o contato ao canal do Umbler Talk usado na conversa."""

    identificador: EntityText = Field(default=None, description="Numero ou conta do canal.")


class TRABALHA_EM(EdgeModel):
    """Liga o contato a empresa onde trabalha ou que representa."""

    cargo: EntityText = Field(
        default=None, description="Cargo do contato: socio, gerente, desenvolvedor, marketing."
    )


class INTERESSADO_EM(EdgeModel):
    """Liga o contato a um produto da Umbler que ele usa, pediu ou avalia."""

    situacao: EntityText = Field(
        default=None, description="Se ja usa, esta avaliando, pediu orcamento ou cancelou."
    )


class TEM_NEGOCIACAO(EdgeModel):
    """Liga o contato ou a empresa a uma oportunidade comercial em andamento."""

    prioridade: EntityText = Field(default=None, description="Prioridade do negocio para o time.")


class AVALIA_PLANO(EdgeModel):
    """Liga a negociacao ou o contato ao plano da Umbler em consideracao."""

    motivo: EntityText = Field(
        default=None, description="Por que esse plano faz sentido para o cliente."
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


ENTIDADES: dict[str, type[EntityModel]] = {
    "Vendedor": Vendedor,
    "EmpresaCliente": EmpresaCliente,
    "ProdutoUmbler": ProdutoUmbler,
    "Plano": Plano,
    "Negociacao": Negociacao,
    "Objecao": Objecao,
    "Concorrente": Concorrente,
    "Setor": Setor,
    "Tag": Tag,
    "Canal": Canal,
}

ARESTAS = {
    "ATENDIDO_POR": (ATENDIDO_POR, [EntityEdgeSourceTarget(source="User", target="Vendedor")]),
    "ATENDIDO_NO_SETOR": (
        ATENDIDO_NO_SETOR,
        [EntityEdgeSourceTarget(source="User", target="Setor")],
    ),
    "ATENDIMENTO_ENCERRADO": (
        ATENDIMENTO_ENCERRADO,
        [EntityEdgeSourceTarget(source="User", target="Setor")],
    ),
    "TEM_TAG": (TEM_TAG, [EntityEdgeSourceTarget(source="User", target="Tag")]),
    "CONTATO_PELO_CANAL": (
        CONTATO_PELO_CANAL,
        [EntityEdgeSourceTarget(source="User", target="Canal")],
    ),
    "TRABALHA_EM": (TRABALHA_EM, [EntityEdgeSourceTarget(source="User", target="EmpresaCliente")]),
    "INTERESSADO_EM": (
        INTERESSADO_EM,
        [EntityEdgeSourceTarget(source="User", target="ProdutoUmbler")],
    ),
    "TEM_NEGOCIACAO": (
        TEM_NEGOCIACAO,
        [
            EntityEdgeSourceTarget(source="User", target="Negociacao"),
            EntityEdgeSourceTarget(source="EmpresaCliente", target="Negociacao"),
        ],
    ),
    "AVALIA_PLANO": (
        AVALIA_PLANO,
        [
            EntityEdgeSourceTarget(source="Negociacao", target="Plano"),
            EntityEdgeSourceTarget(source="User", target="Plano"),
        ],
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
}

ENTITY_TYPES: tuple[str, ...] = tuple(ENTIDADES)
EDGE_TYPES: tuple[str, ...] = tuple(ARESTAS)


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
