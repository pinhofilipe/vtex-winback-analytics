# VTEX Win-back & Análise de Clientes

Scripts em Python para extrair dados do painel administrativo da VTEX (via API oficial de Orders) e transformá-los em informação acionável para ações de marketing e retenção de clientes.

## Sobre o projeto

Este projeto nasceu de uma necessidade prática: identificar clientes com pedidos cancelados para ações de win-back, e entender melhor o comportamento de compra da base de clientes (quem compra uma vez só, quem usa cupons específicos). Os dados são extraídos diretamente da API da VTEX, tratados e exportados em formato pronto para uso em Excel/Power BI ou para acionar equipes de contato (SMS, WhatsApp, e-mail).

## Funcionalidades

### 1. Extração diária de pedidos cancelados (`extrair_pedidos_cancelados.py`)

- Busca pedidos cancelados do dia anterior (D-1), sempre no fuso horário de São Paulo
- Enriquece cada pedido com e-mail e telefone real do cliente (removendo o mascaramento de privacidade da VTEX)
- Converte valores monetários de centavos para reais
- Mantém um histórico acumulado em banco SQLite (sem duplicar pedidos já processados)
- Gera um CSV formatado para Excel em português (separador `;`, decimal com vírgula)
- Pensado para rodar automaticamente todo dia via Agendador de Tarefas do Windows

### 2. Análise de clientes (`analise_clientes_unicos_e_cupom.py`)

- Identifica clientes que compraram **apenas uma vez** em um período configurável (considera só pedidos com status aprovado/faturado como compra real)
- Suporte para identificar clientes que usaram um **cupom específico** (desativado por padrão, configurável no topo do script)
- Divide o período de busca em janelas semanais para não esbarrar no limite de paginação da API da VTEX (30 páginas por consulta)

## Tecnologias usadas

- **Python** — pandas, requests, python-dotenv
- **SQLite** — persistência do histórico de pedidos cancelados
- **VTEX Orders API** — fonte dos dados (List Orders e Get Order)

## Como executar

1. Clone o repositório e instale as dependências:
   ```
   pip install requests pandas python-dotenv
   ```

2. Crie um arquivo `.env` na raiz do projeto com suas credenciais da VTEX:
   ```
   VTEX_ACCOUNT_NAME=nome-da-conta
   VTEX_ENVIRONMENT=vtexcommercestable
   VTEX_APP_KEY=sua_chave
   VTEX_APP_TOKEN=seu_token
   ```
   > As chaves são geradas em: VTEX Admin > Configurações da conta > Conta > Segurança > Chaves de aplicação (permissão de leitura em OMS é suficiente).

3. Rode o script desejado:
   ```
   python extrair_pedidos_cancelados.py
   python analise_clientes_unicos_e_cupom.py
   ```

## Observações importantes sobre a API da VTEX

- Valores monetários são retornados em **centavos**, não em reais.
- A VTEX mascara o e-mail do cliente por padrão (recurso *Conversation Tracker*); os scripts já tratam isso para recuperar o e-mail real quando possível.
- A List Orders tem um limite de 30 páginas por consulta — por isso a análise histórica divide o período em janelas menores.
- A API guarda dados de pedidos por, no máximo, 2 anos.

## Segurança

O arquivo `.env`, a pasta `extracoes/`, a pasta `analises/` e os arquivos `.db` **não são versionados** (veja `.gitignore`) — contêm credenciais de API e dados pessoais de clientes, que não devem ir para um repositório Git.

## Próximos passos

- Automação via Agendador de Tarefas (Windows) ou GitHub Actions
- Ativação da análise de clientes por cupom específico
- Expansão para outros dados do painel VTEX (catálogo, estoque, precificação)
