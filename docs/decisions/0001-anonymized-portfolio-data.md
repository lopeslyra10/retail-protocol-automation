# ADR 0001 — Publicar somente dados sintéticos

- Status: aceito
- Data: 2026-09-19

## Contexto

O projeto nasceu de um fluxo operacional real e, por isso, sua primeira versão local continha nomes de unidades, endereços, números de ordens de serviço e exemplos extraídos de e-mails de trabalho. Esses dados não são necessários para demonstrar a arquitetura e não devem fazer parte de um repositório público.

## Decisão

Criar uma edição de portfólio antes do primeiro commit público:

- substituir cadastros por unidades e endereços fictícios;
- trocar OS, períodos e quantidades por exemplos sintéticos;
- tornar marca e prefixos de arquivo configuráveis;
- não versionar e-mails recebidos, PDFs ou ZIPs da operação;
- documentar explicitamente a anonimização.

## Consequências

### Positivas

- o histórico Git completo é seguro para publicação;
- recrutadores conseguem executar o produto sem dados externos;
- testes permanecem determinísticos;
- a solução deixa de parecer acoplada a um único cliente.

### Negativas

- os dados demonstrativos não reproduzem todas as irregularidades encontradas na operação;
- exemplos complexos precisam ser recriados artificialmente nos testes.

## Alternativas rejeitadas

- **Apagar dados reais em um commit posterior:** inadequado, pois o conteúdo continuaria acessível no histórico.
- **Publicar o repositório como privado:** reduziria o risco, mas contrariaria o objetivo de portfólio público.
