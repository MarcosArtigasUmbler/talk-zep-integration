---
source: Modelo de arquivo de conhecimento
created_at: 2026-01-01T00:00:00Z
---

Arquivos que comecam com `_` sao ignorados pelo `scripts/load_knowledge.py`.
Copie este modelo para `knowledge/<assunto>.md`, substitua o conteudo por
informacao REAL e verificada da Umbler e rode `python -m scripts.load_knowledge`.

Regras para este grafo (ele e lido pelo agente de qualquer contato):

- Um assunto por arquivo: planos de um produto, politica de desconto, periodo
  de teste, integracoes, seguranca/LGPD, respostas padrao de suporte.
- Frases completas e afirmativas, com nomes e valores exatos. O extrator
  transforma cada frase em fatos; frase vaga vira fato vago.
- Nunca coloque dado de cliente aqui. Isso vai no grafo do proprio contato,
  via webhook.
- Use `created_at` no cabecalho para dizer desde quando o conteudo vale
  (tabela de preco nova, politica nova). O Zep invalida o fato antigo.

## Exemplo de estrutura

O Umbler Talk e a plataforma de atendimento da Umbler para WhatsApp, com
multiplos atendentes no mesmo numero, chatbot e relatorios.

O plano X do Umbler Talk custa R$ 000 por atendente por mes no contrato anual
e inclui ate N numeros de WhatsApp.

Desconto acima de 00% precisa de aprovacao do gerente comercial.
