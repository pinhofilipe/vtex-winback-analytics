# VTEX Win-back & Análise de Clientes

Scripts em Python para extrair dados do painel administrativo da VTEX (via API oficial) e transformá-los em informação acionável para ações de marketing, retenção de clientes e curadoria de campanhas.

## Sobre o projeto

Este projeto nasceu de uma necessidade prática: identificar clientes com pedidos cancelados para ações de win-back, entender melhor o comportamento de compra da base de clientes, consolidar exportações de dados bagunçadas em algo utilizável, e mais recentemente, apoiar a curadoria manual de produtos candidatos a sessões de Outlet com base no saldo de estoque por filial. Os dados são extraídos diretamente da API da VTEX, tratados e exportados em formato pronto para uso em Excel/Power BI ou para acionar equipes de contato (SMS, WhatsApp, e-mail).

## Funcionalidades

### 1. Extração diária de pedidos cancelados (`extrair_pedidos_cancelados.py`)

- Busca pedidos cancelados do dia anterior (D-1), sempre no fuso horário de São Paulo
- Enriquece cada pedido com e-mail e telefone real do cliente (removendo o mascaramento de privacidade da VTEX)
- Converte valores monetários de centavos para reais
- Mantém um histórico acumulado em banco SQLite (sem duplicar pedidos já processados)
- Gera um CSV formatado para Excel em português (separador `;`, decimal com vírgula)
- Pensado para rodar automaticamente todo dia via Agendador de Tarefas do Windows

### 2. Análise de clientes e cupons (`analise_clientes_unicos_e_cupom.py`)

- Identifica clientes que compraram **apenas uma vez** em um período configurável (considera só pedidos com status aprovado/faturado como compra real)
- Suporte para identificar clientes que usaram um **cupom específico** (desativado por padrão, configurável no topo do script)
- Divide o período de busca em janelas semanais para não esbarrar no limite de paginação da API da VTEX (30 páginas por consulta)

### 3. Clientes de compra única (`1compra.py`)

- Levanta os clientes que fizeram **apenas 1 compra** no e-commerce, para uso em ações de reativação/win-back direcionadas.

### 4. Consolidação de exportações de clientes (`consolida_clientes.py`)

- Limpa e une exportações de CSV da VTEX (muitas vezes bagunçadas, com encodings e delimitadores diferentes) em uma única planilha XLSX deduplicada
- Trata codificação UTF-8-BOM, delimitador `;`, remoção de DDI de telefone, e casamento de colunas por lista de aliases (fortes e fracos) para evitar falsos positivos
- Gera diagnóstico de colunas não reconhecidas quando o resultado vem vazio

### 5. Candidatos a Outlet por saldo de estoque (`estoque_outlet.py`)

- Varre todo o catálogo ativo de SKUs da conta
- Consulta o saldo disponível (Total − Reservado) especificamente nas filiais `INF50` e `INF01` via API de Inventário/Logística
- Filtra apenas produtos **categorizados** (marca + categoria associadas) com saldo entre 1 e 3 unidades nessas filiais
- Enriquece os resultados com marca, categoria, preço de/por e % de desconto
- Exporta uma planilha XLSX ordenada por maior desconto, para **curadoria manual** — nada é publicado ou alterado automaticamente no catálogo
- Possui `TEST_MODE` para validar o funcionamento numa amostra pequena antes de rodar o catálogo inteiro
- Usa `requests.Session()` com pool de conexões e execução em threads para acelerar a varredura, já que a API da VTEX não oferece consulta de estoque em lote (só por SKU individual)

## Tecnologias usadas

- **Python** — pandas, requests, python-dotenv, openpyxl
- **concurrent.futures (ThreadPoolExecutor)** — paralelização de chamadas à API
- **SQLite** — persistência do histórico de pedidos cancelados
- **VTEX Orders API** — fonte dos dados de pedidos (List Orders e Get Order)
- **VTEX Catalog & Logistics API** — fonte dos dados de catálogo, categorização e estoque por filial

## Como executar

1. Clone o repositório e instale as dependências:
   ```
   pip install requests pandas python-dotenv openpyxl
   ```

2. Crie um arquivo `.env` na raiz do projeto com suas credenciais da VTEX:
   ```
   VTEX_ACCOUNT_NAME=nome-da-conta
   VTEX_ENVIRONMENT=vtexcommercestable
   VTEX_APP_KEY=sua_chave
   VTEX_APP_TOKEN=seu_token
   ```
   > As chaves são geradas em: VTEX Admin > Configurações da conta > Conta > Segurança > Chaves de aplicação (permissão de leitura em OMS e Catálogo é suficiente).

3. Rode o script desejado:
   ```
   python extrair_pedidos_cancelados.py
   python analise_clientes_unicos_e_cupom.py
   python 1compra.py
   python consolida_clientes.py
   python estoque_outlet.py
   ```

## Observações importantes sobre a API da VTEX

- Valores monetários são retornados em **centavos**, não em reais.
- A VTEX mascara o e-mail do cliente por padrão (recurso *Conversation Tracker*); os scripts já tratam isso para recuperar o e-mail real quando possível.
- A List Orders tem um limite de 30 páginas por consulta — por isso a análise histórica divide o período em janelas menores.
- A API guarda dados de pedidos por, no máximo, 2 anos.
- Não existe endpoint de consulta de estoque em lote por filial — a API de Inventário só responde por SKU individual, o que torna varreduras de catálogo completo inerentemente mais lentas.

## Segurança

- O arquivo `.env`, a pasta `extracoes/`, a pasta `analises/`, arquivos `.db`, `.csv` e `.xlsx` **não são versionados** (veja `.gitignore`) — contêm credenciais de API e dados pessoais de clientes, que não devem ir para um repositório Git.
- **Nunca** gere exportações de dados de clientes (planilhas, CSVs) dentro da pasta do repositório. Sempre salve fora da árvore versionada, mesmo com o `.gitignore` configurado — reduz o risco de um `git add .` acidental subir dados sensíveis.
- Antes de qualquer `git push`, rode `git status` e confira manualmente a lista de arquivos que serão enviados.

## Próximos passos

- Rodar `estoque_outlet.py` no catálogo completo (hoje testado em amostra) e calibrar `MAX_WORKERS` conforme o comportamento de rate limit da conta
- Automação via Agendador de Tarefas (Windows) ou GitHub Actions para os scripts de extração recorrente
- Ativação da análise de clientes por cupom específico em `analise_clientes_unicos_e_cupom.py`
- Expansão para outros dados do painel VTEX (precificação, promoções ativas)
