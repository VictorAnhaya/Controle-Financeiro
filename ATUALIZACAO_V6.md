# Atualização V6 — Bolotti Reis + WBK

Esta versão substitui os arquivos da versão anterior sem exigir a exclusão do banco de dados existente.

## O que foi incluído

- aba inicial **Consolidado** para Bolotti Reis + WBK;
- filtro global por empresa em lançamentos, clientes, notas fiscais, ranking, orçamentos, metas e relatórios;
- empresa obrigatória ao criar um lançamento ou anexar uma nota fiscal;
- identificação automática da planilha: São José dos Pinhais = Bolotti Reis e Curitiba = WBK;
- importação consolidada com separação automática por município;
- metas e orçamentos independentes para cada empresa e para o consolidado;
- cenários pessimista, base, otimista e estratégico;
- projeção dos próximos seis meses com sazonalidade;
- migração automática dos dados já cadastrados.
- painel de usuários restrito ao administrador inicial, inclusive após troca de login no mesmo navegador.

## Como substituir

1. Faça uma cópia de segurança do banco `data/finance.db` e da pasta `data/documents`.
2. Extraia este ZIP.
3. Substitua os arquivos do projeto pelos arquivos desta pasta.
4. Não apague o banco nem a pasta de documentos do ambiente em produção.
5. Envie os arquivos atualizados ao GitHub e aguarde o novo deploy do Render.

Na primeira inicialização, o sistema cria as duas empresas e classifica os lançamentos antigos pelo município registrado nas observações. Usuários, senhas, sessões, clientes e documentos existentes são preservados.

## Conferência da planilha de origem

- Bolotti Reis: 14 notas ativas, total de R$ 123.665,37.
- WBK: 11 notas ativas, total de R$ 106.871,76.
- Consolidado: 25 notas ativas, total de R$ 230.537,13.
- Canceladas: 4 notas, total de R$ 48.692,37, classificadas na WBK conforme o município da planilha.

Todos os testes automatizados foram executados também com a planilha original anexada.
