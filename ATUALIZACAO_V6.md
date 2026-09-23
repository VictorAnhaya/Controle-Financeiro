# Atualização V6.5 — BRC + WBK

Esta versão substitui os arquivos da versão anterior sem exigir a exclusão do banco de dados existente.

## O que foi incluído

- aba inicial **Consolidado** para BRC + WBK;
- filtro global por empresa em lançamentos, clientes, notas fiscais, ranking, orçamentos, metas e relatórios;
- empresa obrigatória ao criar um lançamento ou anexar uma nota fiscal;
- identificação automática da planilha: São José dos Pinhais = BRC e Curitiba = WBK;
- importação consolidada com separação automática por município;
- metas e orçamentos independentes para cada empresa e para o consolidado;
- cenários pessimista, base, otimista e estratégico;
- projeção dos próximos seis meses com sazonalidade;
- migração automática dos dados já cadastrados.
- painel de usuários restrito ao administrador inicial, inclusive após troca de login no mesmo navegador.
- cadastro de empresas integrado ao consolidado e a todos os seletores;
- exclusão destacada na tabela de lançamentos;
- botão de fechar/cancelar lançamento sem validação dos campos obrigatórios;
- filtro mensal próprio na aba de clientes.
- inicialização no Render corrigida para o comando `bash start.sh`, sem dependência de Flask.
- exclusão de empresas pela aba administrativa, bloqueada quando há lançamentos, notas, metas ou orçamentos vinculados;
- exclusão de notas fiscais e do arquivo anexado, sem apagar um lançamento financeiro já criado.
- substituição automática da antiga empresa Bolotti Reis por BRC, preservando todos os dados vinculados;

## Como substituir

1. Faça uma cópia de segurança do banco `data/finance.db` e da pasta `data/documents`.
2. Extraia este ZIP.
3. Substitua os arquivos do projeto pelos arquivos desta pasta.
4. Não apague o banco nem a pasta de documentos do ambiente em produção.
5. Envie os arquivos atualizados ao GitHub e aguarde o novo deploy do Render.

Na primeira inicialização, o sistema cria as duas empresas e classifica os lançamentos antigos pelo município registrado nas observações. Usuários, senhas, sessões, clientes e documentos existentes são preservados.

## Conferência da planilha de origem

- BRC: 14 notas ativas, total de R$ 123.665,37.
- WBK: 11 notas ativas, total de R$ 106.871,76.
- Consolidado: 25 notas ativas, total de R$ 230.537,13.
- Canceladas: 4 notas, total de R$ 48.692,37, classificadas na WBK conforme o município da planilha.

Todos os testes automatizados foram executados também com a planilha original anexada.
