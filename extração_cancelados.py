"""
Extração de pedidos cancelados via VTEX Orders API (List Orders).

Requisitos:
    pip install requests pandas python-dotenv

Configuração (.env na mesma pasta):
    VTEX_ACCOUNT_NAME=nome-da-conta
    VTEX_ENVIRONMENT=vtexcommercestable
    VTEX_APP_KEY=xxxxxxxx
    VTEX_APP_TOKEN=xxxxxxxx
"""

import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

ACCOUNT_NAME = os.getenv("VTEX_ACCOUNT_NAME")
ENVIRONMENT = os.getenv("VTEX_ENVIRONMENT", "vtexcommercestable")
APP_KEY = os.getenv("VTEX_APP_KEY")
APP_TOKEN = os.getenv("VTEX_APP_TOKEN")

BASE_URL = f"https://{ACCOUNT_NAME}.{ENVIRONMENT}.com.br/api/oms/pvt/orders"

HEADERS = {
    "X-VTEX-API-AppKey": APP_KEY,
    "X-VTEX-API-AppToken": APP_TOKEN,
    "Accept": "application/json",
}

# Campos que queremos manter da lista "enxuta" de win-back.
# A List Orders retorna um resumo por pedido; não traz todos os campos do
# pedido completo (isso exigiria um GET /orders/{orderId} por pedido).
# Obs: 'statusDescription' foi deixado de fora de propósito — a própria
# VTEX marca esse campo como obsoleto e ele pode vir sempre vazio.
CAMPOS_RELEVANTES = [
    "orderId",
    "creationDate",
    "clientName",
    "totalValue",
    "status",
    "salesChannel",
]


FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")


def janela_dia_anterior_utc() -> tuple[str, str]:
    """
    Calcula o intervalo do dia anterior (D-1) completo, 00:00 a 23:59:59,
    no fuso de São Paulo, e converte para UTC (formato que a VTEX espera).

    Isso importa porque a VTEX guarda creationDate em UTC. Se truncássemos
    direto em UTC, a meia-noite de "ontem" no Brasil (UTC-3) cairia em outro
    instante em UTC, pegando pedaço errado de dois dias diferentes.
    """
    hoje_brasil = datetime.now(FUSO_BRASIL).date()
    ontem_brasil = hoje_brasil - timedelta(days=1)

    inicio_local = datetime.combine(ontem_brasil, datetime.min.time(), tzinfo=FUSO_BRASIL)
    fim_local = datetime.combine(ontem_brasil, datetime.max.time(), tzinfo=FUSO_BRASIL)

    inicio_utc = inicio_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    fim_utc = fim_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.999Z")

    return inicio_utc, fim_utc


def buscar_pedidos_cancelados(data_inicio: str, data_fim: str, per_page: int = 100) -> pd.DataFrame:
    """
    Busca pedidos com status 'canceled' criados entre data_inicio e data_fim
    (strings já formatadas em UTC, prontas para o filtro f_creationDate da VTEX).
    """
    if not all([ACCOUNT_NAME, APP_KEY, APP_TOKEN]):
        raise EnvironmentError(
            "Faltam variáveis de ambiente. Confira o arquivo .env "
            "(VTEX_ACCOUNT_NAME, VTEX_APP_KEY, VTEX_APP_TOKEN)."
        )

    todos_pedidos = []
    pagina = 1

    while True:
        params = {
            "f_status": "canceled",
            "f_creationDate": f"creationDate:[{data_inicio} TO {data_fim}]",
            "page": pagina,
            "per_page": per_page,
            "orderBy": "creationDate,desc",
        }

        resposta = requests.get(BASE_URL, headers=HEADERS, params=params)

        if resposta.status_code == 429:
            # Limite de requisições atingido — a VTEX recomenda esperar
            # antes de tentar de novo, em vez de insistir imediatamente.
            print("Rate limit atingido (429). Aguardando 60s...")
            time.sleep(60)
            continue

        resposta.raise_for_status()
        dados = resposta.json()

        lista_pagina = dados.get("list", [])
        if not lista_pagina:
            break

        todos_pedidos.extend(lista_pagina)

        # Quando a página retorna menos itens que o per_page pedido,
        # chegamos ao fim dos resultados.
        if len(lista_pagina) < per_page:
            break

        pagina += 1

    df = pd.DataFrame(todos_pedidos)

    if df.empty:
        print("Nenhum pedido cancelado encontrado no período.")
        return df

    colunas_existentes = [c for c in CAMPOS_RELEVANTES if c in df.columns]
    df = df[colunas_existentes]

    return df


def buscar_contato_pedido(order_id: str) -> dict:
    """
    Busca email e telefone de um pedido específico via Get Order
    (a List Orders não traz esses campos, só o detalhe individual).
    """
    url = f"{BASE_URL}/{order_id}"

    while True:
        resposta = requests.get(url, headers=HEADERS)

        if resposta.status_code == 429:
            print(f"Rate limit atingido no pedido {order_id}. Aguardando 60s...")
            time.sleep(60)
            continue

        resposta.raise_for_status()
        dados = resposta.json()
        break

    perfil = dados.get("clientProfileData") or {}
    return {
        "email": perfil.get("email"),
        "telefone": perfil.get("phone"),
    }


# Padrão do "Conversation Tracker" da VTEX no modo 'soft': o e-mail real
# vem seguido de um sufixo técnico do tipo -HASH.ct.vtex.com.br, usado
# para rastrear respostas de e-mail vinculadas ao pedido. Removemos esse
# sufixo para recuperar o e-mail real do cliente.
PADRAO_SUFIXO_CONVERSATION_TRACKER = re.compile(r"-[0-9a-fA-F]+\.ct\.vtex\.com\.br$")


def limpar_email_mascarado(email):
    if not isinstance(email, str):
        return email
    return PADRAO_SUFIXO_CONVERSATION_TRACKER.sub("", email)


def enriquecer_com_contato(df: pd.DataFrame) -> pd.DataFrame:
    """
    Para cada pedido da lista, busca email/telefone via Get Order e
    adiciona como novas colunas. Faz 1 chamada extra de API por pedido —
    por isso inclui uma pequena pausa entre chamadas, para reduzir o
    risco de estourar o limite de requisições da VTEX.
    """
    df = df.copy()
    emails = []
    telefones = []
    total = len(df)

    for i, order_id in enumerate(df["orderId"], start=1):
        print(f"Buscando contato do pedido {i}/{total}: {order_id}")
        contato = buscar_contato_pedido(order_id)
        emails.append(limpar_email_mascarado(contato["email"]))
        telefones.append(contato["telefone"])
        time.sleep(0.3)

    df["email_cliente"] = emails
    df["telefone_cliente"] = telefones

    return df


def polir_dados(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica limpeza/formatação na lista bruta vinda da VTEX:
      - totalValue: a VTEX retorna valores monetários em centavos
        (ex: 3780 = R$ 37,80). Convertemos para reais.
      - creationDate: vem em UTC; convertemos para o fuso do Brasil e
        separamos em data e hora, mais fácil de ler e filtrar no Power BI.
      - Renomeia colunas técnicas (em inglês) para nomes de negócio.
    """
    df = df.copy()

    if "totalValue" in df.columns:
        df["totalValue"] = df["totalValue"] / 100

    if "creationDate" in df.columns:
        datas_utc = pd.to_datetime(df["creationDate"], utc=True)
        datas_brasil = datas_utc.dt.tz_convert(FUSO_BRASIL)
        df["data_cancelamento"] = datas_brasil.dt.strftime("%Y-%m-%d")
        df["hora_cancelamento"] = datas_brasil.dt.strftime("%H:%M:%S")
        df = df.drop(columns=["creationDate"])

    df = df.rename(
        columns={
            "orderId": "id_pedido",
            "clientName": "nome_cliente",
            "totalValue": "valor_total_reais",
            "status": "status_pedido",
            "salesChannel": "canal_venda",
        }
    )

    colunas_ordenadas = [
        c
        for c in [
            "id_pedido",
            "nome_cliente",
            "email_cliente",
            "telefone_cliente",
            "valor_total_reais",
            "status_pedido",
            "canal_venda",
            "data_cancelamento",
            "hora_cancelamento",
        ]
        if c in df.columns
    ]
    df = df[colunas_ordenadas]

    df = df.sort_values(by=["data_cancelamento", "hora_cancelamento"], ascending=False)
    df = df.reset_index(drop=True)

    return df


def salvar_csv(df: pd.DataFrame, pasta_saida: str = "extracoes") -> str:
    """
    Salva com ';' como separador (não ',') porque o Excel em português
    do Brasil usa vírgula como separador decimal — abrindo um CSV separado
    por vírgula, ele junta tudo numa coluna só em vez de organizar.
    Também formata valores monetários com vírgula decimal (898,99 em vez
    de 898.99), para o Excel reconhecer como número, não como texto.
    """
    df = df.copy()

    if "valor_total_reais" in df.columns:
        df["valor_total_reais"] = (
            df["valor_total_reais"].map(lambda v: f"{v:.2f}".replace(".", ","))
        )

    os.makedirs(pasta_saida, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = os.path.join(pasta_saida, f"pedidos_cancelados_{timestamp}.csv")
    df.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";")
    return caminho


if __name__ == "__main__":
    # Sempre extrai o dia anterior completo (D-1), no fuso do Brasil.
    inicio, fim = janela_dia_anterior_utc()
    df_pedidos = buscar_pedidos_cancelados(data_inicio=inicio, data_fim=fim)

    if not df_pedidos.empty:
        df_pedidos = enriquecer_com_contato(df_pedidos)
        df_pedidos = polir_dados(df_pedidos)
        caminho_arquivo = salvar_csv(df_pedidos)
        print(f"{len(df_pedidos)} pedidos cancelados extraídos.")
        print(f"Salvo em: {caminho_arquivo}")