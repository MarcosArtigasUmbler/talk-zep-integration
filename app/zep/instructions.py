"""Instrucoes de idioma e dominio para a extracao e os resumos do Zep.

Regras aprendidas (APRENDIZADOS-ZEP.md, secao 8):

* cada instrucao tem no maximo 100 caracteres;
* descreva o TEXTO desejado ("Escreva o resumo em..."), nunca de ordem ao
  assistente ("Responda sempre em...") -- a ordem ecoa dentro do resumo;
* sem ``user_ids``/``graph_ids`` a instrucao vale para o projeto todo, o que
  e o desejado: os grafos dos contatos tambem precisam de portugues.
"""

from __future__ import annotations

from zep_cloud.types.custom_instruction import CustomInstruction
from zep_cloud.types.user_instruction import UserInstruction

from app.config import get_settings
from app.zep.client import get_zep

INSTRUCOES_RESUMO: list[tuple[str, str]] = [
    ("idioma", "Escreva o resumo em portugues do Brasil. Nunca escreva em ingles."),
    (
        "conteudo",
        "Empresa do contato, cargo, produtos da Umbler em uso ou avaliacao, plano e objecao.",
    ),
    ("atendimento", "Setor e atendente atuais, tags aplicadas e o ultimo assunto tratado."),
    ("proximo_passo", "Ao final, o que trava o avanco e qual o proximo passo combinado."),
    ("limites", "Apenas fatos presentes nos dados; sem valores, prazos ou aprovacoes estimados."),
]

INSTRUCOES_GRAFO: list[tuple[str, str]] = [
    ("idioma", "Fatos redigidos em portugues do Brasil, com nomes proprios preservados."),
    ("papeis", "O usuario e o contato/cliente; o atendente da Umbler e entidade separada."),
    ("pronomes", "'Nos' e 'a gente' ditos pelo atendente referem-se a Umbler."),
    ("perguntas", "Pergunta nao e fato: 'o gerente aprovou?' nao registra aprovacao."),
    ("adiado", "'Agora nao' e negociacao adiada, nunca perdida."),
]

MAX_INSTRUCTION_CHARS = 100

for _name, _text in INSTRUCOES_RESUMO + INSTRUCOES_GRAFO:
    assert len(_text) <= MAX_INSTRUCTION_CHARS, f"instrucao {_name!r} passa de 100 caracteres"


async def apply_instructions(*, replace: bool = True) -> dict[str, list[str]]:
    zep = get_zep()
    settings = get_settings()
    removed: dict[str, list[str]] = {"resumo": [], "grafo": []}

    if replace:
        atuais = await zep.user.list_user_summary_instructions()
        nomes = [i.name for i in (atuais.instructions or []) if i.name]
        if nomes:
            await zep.user.delete_user_summary_instructions(instruction_names=nomes)
            removed["resumo"] = nomes
        # list usa graph_id (singular); delete usa graph_ids (plural).
        antigas = await zep.graph.list_custom_instructions(graph_id=settings.zep_org_graph_id)
        nomes_grafo = [i.name for i in (antigas.instructions or []) if i.name]
        if nomes_grafo:
            await zep.graph.delete_custom_instructions(
                graph_ids=[settings.zep_org_graph_id], instruction_names=nomes_grafo
            )
            removed["grafo"] = nomes_grafo

    await zep.user.add_user_summary_instructions(
        instructions=[UserInstruction(name=n, text=t) for n, t in INSTRUCOES_RESUMO]
    )
    await zep.graph.add_custom_instructions(
        instructions=[CustomInstruction(name=n, text=t) for n, t in INSTRUCOES_GRAFO]
    )
    return removed
