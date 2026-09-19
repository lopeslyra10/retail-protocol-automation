# Contribuindo

## Ambiente

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
```

## Fluxo recomendado

1. crie uma branch curta a partir de `main`;
2. descreva no commit o motivo da mudança, não apenas o arquivo alterado;
3. adicione testes para regras novas ou corrigidas;
4. execute lint, formatação e testes antes do push;
5. abra um pull request pequeno, com contexto e forma de validação.

## Convenção de commits

O projeto usa Conventional Commits:

- `feat:` nova capacidade percebida pelo usuário;
- `fix:` correção de comportamento;
- `refactor:` mudança interna sem alterar o contrato;
- `test:` cobertura ou infraestrutura de testes;
- `docs:` documentação;
- `ci:` automação de integração contínua;
- `chore:` manutenção sem impacto funcional.

Mensagens devem explicar a intenção. Exemplo:

```text
fix(import): reject duplicated stores before saving

Duplicate rows could generate two protocols for the same destination. The parser
now marks the table as invalid so the operator must review the source first.
```

## Verificações obrigatórias

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Nunca inclua e-mails reais, dados de clientes, credenciais, PDFs operacionais ou arquivos ZIP gerados.
