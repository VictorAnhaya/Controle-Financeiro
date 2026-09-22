# Bolotti Finance — Versão 4

Aplicação de controle financeiro criada a partir do relatório de NFS-e do Grupo Bolotti Reis. A base inicial contém as 25 notas ativas de agosto de 2026 (R$ 230.537,13) e as 4 notas canceladas (R$ 48.692,37), conciliadas com a planilha de origem.

## Recursos incluídos

- painel mensal com receitas, despesas, saldo e contas a pagar;
- gráficos de fluxo diário e gastos por categoria;
- cadastro, edição, busca, filtros e exclusão de lançamentos;
- controle de status: pago/recebido, pendente, vencido e cancelado;
- categorias, centros de custo, fornecedor/cliente, documento e observações;
- orçamento mensal por categoria, com alertas de consumo e estouro;
- relatório gerencial e exportação dos lançamentos em CSV;
- importação da mesma estrutura Excel de NFS-e, com deduplicação automática;
- central de documentos fiscais com armazenamento local do arquivo original;
- leitura estruturada de XML de NF-e e NFS-e;
- extração de PDF com texto e imagens por OCR;
- conferência dos campos antes de criar uma despesa ou receita;
- vínculo permanente entre o documento fiscal e o lançamento;
- detecção de arquivo duplicado por assinatura SHA-256;
- ranking automático de clientes por faturamento ativo;
- agrupamento prioritário por CNPJ/CPF, evitando duplicidade por variação do nome;
- quantidade de notas, participação no faturamento e ticket médio por cliente;
- indicadores de concentração dos 3 e 5 maiores clientes;
- classificação mensal ou consolidada de todo o histórico;
- banco de dados SQLite local e valores armazenados em centavos;
- interface responsiva para computador, tablet e celular.
- tela de login com sessões seguras armazenadas no banco;
- criação do primeiro administrador no primeiro acesso;
- aba de usuários exclusiva para administradores;
- criação, edição, alteração de senha, ativação e desativação de usuários.

## Como executar no VS Code — Windows 11

1. Extraia o projeto e abra a pasta `bolotti-finance` no VS Code.
2. Abra o terminal integrado e verifique o Python (`python --version`).
3. Crie o ambiente virtual:

```powershell
python -m venv .venv
```

4. Instale as dependências usando diretamente o Python do ambiente:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

5. Inicie o servidor:

```powershell
.\.venv\Scripts\python.exe run.py
```

6. Abra `http://127.0.0.1:8080` no navegador.
7. No primeiro acesso, crie o usuário administrador. Depois, use a aba **Usuários** para cadastrar as demais pessoas.

O app principal não precisa de pacotes externos. O comando acima instala os recursos de Excel, PDF e imagem.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Leitura de notas fiscais

- **XML:** funciona com a estrutura oficial de NF-e e com os principais layouts de NFS-e. É a opção mais precisa.
- **PDF com texto:** identifica número, emissão, CNPJ, emitente, valor total e chave quando disponíveis.
- **PNG/JPG/WEBP/TIFF:** utiliza OCR. Além das dependências Python, o programa Tesseract OCR deve estar instalado no Windows e disponível no `PATH`.
- **PDF escaneado:** exporte a página como PNG ou JPG para utilizar o OCR.

Toda extração de PDF ou imagem deve ser conferida na tela antes da criação do lançamento. O arquivo original é armazenado em `data/documents` e não é enviado para serviços externos.

Não existem arquivos `.bat` nesta versão; toda execução é feita pelo terminal do VS Code.

## Estrutura técnica

```text
finance_app/
  auth.py         autenticação, senhas, sessões e usuários
  config.py       configuração por variáveis de ambiente
  database.py     conexão, transações e migrações SQLite
  repository.py   consultas e persistência
  service.py      validações e regras financeiras
  importer.py     leitura e conciliação do Excel de NFS-e
  document_reader.py  extração segura de XML, PDF e imagem
  http.py         API HTTP e entrega do frontend
  static/         interface web em HTML, CSS e JavaScript
data/
  initial_transactions.json  base inicial conciliada
tests/
  test_finance.py            testes automatizados
```

A separação entre transporte HTTP, regras de negócio e persistência evita misturar responsabilidades e facilita uma futura migração para uma API corporativa, PostgreSQL ou interface React sem reescrever o domínio financeiro.

## Banco de dados e backup

O banco é criado automaticamente em `data/finance.db`. Para um backup completo, encerre o app e copie o banco junto com a pasta `data/documents`. No computador local, a aplicação fica limitada a `127.0.0.1` por padrão. No Render, o arquivo `render.yaml` ativa um disco persistente. Usuários, senhas e sessões ficam no mesmo banco persistente.

Variáveis opcionais:

- `FINANCE_PORT`: altera a porta padrão `8080`;
- `PORT`: porta fornecida automaticamente pelo Render e priorizada quando existir;
- `FINANCE_DATABASE`: altera o caminho do banco;
- `FINANCE_DATA_DIR`: altera a pasta de dados;
- `FINANCE_HOST`: altera o endereço de escuta. Não use `0.0.0.0` em rede sem adicionar autenticação e HTTPS.

## Publicação no Render

O repositório GitHub deste projeto deve ser **privado**, pois a base inicial contém informações financeiras reais.

1. Crie um repositório privado no GitHub e envie o conteúdo da pasta `bolotti-finance` para a raiz dele.
2. No Render, escolha **New > Blueprint** e conecte esse repositório.
3. O Render localizará o arquivo `render.yaml` e preparará o serviço e o disco persistente.
4. Confirme a criação e aguarde o deploy.
5. Abra o endereço `onrender.com` criado.
6. Na tela de primeiro acesso, cadastre o administrador.
7. Entre na aba **Usuários** para cadastrar os demais acessos sem precisar alterar variáveis no Render.

O Blueprint usa um serviço pago com disco persistente de 1 GB. Essa configuração é necessária porque o plano gratuito perde o banco SQLite e os documentos anexados quando o serviço reinicia. O Dockerfile também instala o Tesseract em português para manter a leitura OCR de imagens no servidor.

As senhas são armazenadas com hash PBKDF2 e salt individual. A sessão expira depois de 12 horas, usuários inativos não conseguem entrar e apenas administradores podem acessar a aba de usuários.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Para também testar a conciliação direta com a planilha original:

```powershell
$env:SOURCE_XLSX="C:\caminho\Relatório_financeiro.xlsx"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
