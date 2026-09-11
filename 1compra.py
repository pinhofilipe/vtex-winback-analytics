"""
Análise histórica de clientes via VTEX Orders API:
  1) Clientes que compraram apenas 1 vez (considerando só pedidos com
     status 'payment-approved' ou 'invoiced' como compra real)
  2) Clientes que compraram utilizando um cupom específico

Diferente do script de win-back diário: aqui processamos um período maior
(últimos 6 meses) e todo pedido precisa de uma chamada extra à API pra
pegar e-mail, telefone e cupom (só vêm no detalhe individual do pedido).
Isso pode demorar bastante dependendo do volume — o script mostra o
progresso conforme roda.

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
from concurrent.futures import ThreadPoolExecutor, as_completed
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

FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")

# ATENÇÃO: preencha com o código exato do cupom que você quer analisar.
CUPOM_ALVO = "DIGITE_AQUI_O_CUPOM"

# Status considerados "compra real" para a análise de compra única.
STATUS_COMPRA_VALIDA = ["payment-approved", "invoiced"]

# Mesmo padrão de mascaramento de e-mail do script de win-back.
PADRAO_SUFIXO_CONVERSATION_TRACKER = re.compile(r"-[0-9a-fA-F]+\.ct\.vtex\.com\.br$")


def limpar_email_mascarado(email):
    if not isinstance(email, str):
        return email
    return PADRAO_SUFIXO_CONVERSATION_TRACKER.sub("", email)


def janela_ultimos_meses(meses: int = 6) -> tuple[datetime, datetime]:
    """
    Calcula um intervalo aproximado dos últimos N meses (30 dias por mês,
    aproximação simples, sem depender de biblioteca externa de datas),
    já convertido para UTC. Retorna objetos datetime (não strings) porque
    precisamos fatiar esse intervalo em pedaços menores depois.
    """
    agora_brasil = datetime.now(FUSO_BRASIL)
    inicio_brasil = agora_brasil - timedelta(days=30 * meses)

    return (
        inicio_brasil.astimezone(ZoneInfo("UTC")),
        agora_brasil.astimezone(ZoneInfo("UTC")),
    )


def formatar_data_vtex(momento: datetime, fim_do_intervalo: bool = False) -> str:
    sufixo = "999" if fim_do_intervalo else "000"
    return momento.strftime(f"%Y-%m-%dT%H:%M:%S.{sufixo}Z")


def gerar_subjanelas(inicio_utc: datetime, fim_utc: datetime, dias_por_janela: int = 7):
    """
    Quebra um período grande em pedaços menores.

    Por quê: a List Orders da VTEX tem um limite de 30 páginas por consulta
    (com per_page=100, isso é 3.000 pedidos). Pedir 6 meses inteiros de uma
    vez estoura esse limite fácil. Fatiando em janelas menores (ex: semanal),
    cada consulta individual fica bem abaixo do teto.
    """
    janelas = []
    atual = inicio_utc
    while atual < fim_utc:
        proximo = min(atual + timedelta(days=dias_por_janela), fim_utc)
        janelas.append((atual, proximo))
        atual = proximo
    return janelas


def buscar_pedidos_por_status(data_inicio: str, data_fim: str, status: str, per_page: int = 100) -> list:
    """Busca todos os pedidos de um único status no período, com paginação."""
    if not all([ACCOUNT_NAME, APP_KEY, APP_TOKEN]):
        raise EnvironmentError(
            "Faltam variáveis de ambiente. Confira o arquivo .env "
            "(VTEX_ACCOUNT_NAME, VTEX_APP_KEY, VTEX_APP_TOKEN)."
        )

    pedidos = []
    pagina = 1
    LIMITE_PAGINAS_VTEX = 30

    while True:
        if pagina > LIMITE_PAGINAS_VTEX:
            print(
                f"AVISO: janela {data_inicio} a {data_fim} (status={status}) "
                f"bateu no limite de {LIMITE_PAGINAS_VTEX} páginas da VTEX "
                f"({LIMITE_PAGINAS_VTEX * per_page} pedidos). Dados dessa janela "
                "estão incompletos — rode de novo com uma janela menor "
                "(reduza dias_por_janela em gerar_subjanelas)."
            )
            break

        params = {
            "f_status": status,
            "f_creationDate": f"creationDate:[{data_inicio} TO {data_fim}]",
            "page": pagina,
            "per_page": per_page,
            "orderBy": "creationDate,desc",
        }

        resposta = requests.get(BASE_URL, headers=HEADERS, params=params)

        if resposta.status_code == 429:
            print(f"Rate limit atingido (status={status}). Aguardando 60s...")
            time.sleep(60)
            continue

        resposta.raise_for_status()
        dados = resposta.json()

        lista_pagina = dados.get("list", [])
        if not lista_pagina:
            break

        pedidos.extend(lista_pagina)

        if len(lista_pagina) < per_page:
            break

        pagina += 1

    return pedidos


def buscar_detalhe_pedido(order_id: str) -> dict:
    """Busca o detalhe completo de um pedido (email, telefone, cupom)."""
    url = f"{BASE_URL}/{order_id}"

    while True:
        resposta = requests.get(url, headers=HEADERS)

        if resposta.status_code == 429:
            print(f"Rate limit atingido no pedido {order_id}. Aguardando 60s...")
            time.sleep(60)
            continue

        resposta.raise_for_status()
        return resposta.json()


def enriquecer_pedidos(df: pd.DataFrame, max_workers: int = 10) -> pd.DataFrame:
    """
    Para cada pedido, busca o detalhe individual e extrai email, telefone
    e cupom utilizado. Faz 1 chamada extra de API por pedido.

    Roda várias chamadas em paralelo (max_workers) em vez de uma por vez —
    a VTEX permite até 5.000 requisições/minuto por conta, bem acima do
    que usamos aqui mesmo com paralelismo. Se começar a aparecer muito
    aviso de rate limit (429) no meio da execução, reduza max_workers.
    """
    df = df.copy()
    order_ids = df["orderId"].tolist()
    total = len(order_ids)
    resultados = [None] * total
    concluidos = 0

    def processar(indice: int, order_id: str) -> tuple:
        detalhe = buscar_detalhe_pedido(order_id)
        perfil = detalhe.get("clientProfileData") or {}
        marketing = detalhe.get("marketingData") or {}
        return indice, {
            "email": limpar_email_mascarado(perfil.get("email")),
            "telefone": perfil.get("phone"),
            "cupom": marketing.get("coupon"),
        }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuros = [
            executor.submit(processar, indice, order_id)
            for indice, order_id in enumerate(order_ids)
        ]

        for futuro in as_completed(futuros):
            indice, dados = futuro.result()
            resultados[indice] = dados
            concluidos += 1
            if concluidos % 100 == 0 or concluidos == total:
                print(f"Processados {concluidos}/{total} pedidos...")

    df["email_cliente"] = [r["email"] for r in resultados]
    df["telefone_cliente"] = [r["telefone"] for r in resultados]
    df["cupom_utilizado"] = [r["cupom"] for r in resultados]

    return df


def analisar_clientes_compra_unica(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa por e-mail (normalizado) e conta pedidos. Retorna só os
    clientes com exatamente 1 pedido no período analisado.

    Atenção: 'compra única no período de 6 meses' não é necessariamente
    'compra única na vida' do cliente — é limitado à janela consultada.
    """
    df = df.copy()
    df = df[df["email_cliente"].notna() & (df["email_cliente"] != "")]
    df["email_normalizado"] = df["email_cliente"].str.strip().str.lower()

    contagem = df.groupby("email_normalizado")["orderId"].count()
    emails_compra_unica = contagem[contagem == 1].index

    resultado = df[df["email_normalizado"].isin(emails_compra_unica)].copy()
    resultado = resultado.drop(columns=["email_normalizado"])

    return resultado


def analisar_clientes_por_cupom(df: pd.DataFrame, cupom_alvo: str) -> pd.DataFrame:
    """Filtra pedidos que usaram o cupom especificado (case-insensitive)."""
    df = df.copy()
    df = df[df["cupom_utilizado"].notna()]
    mascara = df["cupom_utilizado"].str.strip().str.lower() == cupom_alvo.strip().lower()
    return df[mascara].copy()


def salvar_csv(df: pd.DataFrame, caminho: str) -> str:
    df = df.copy()
    if "totalValue" in df.columns:
        df["totalValue"] = (df["totalValue"] / 100).map(lambda v: f"{v:.2f}".replace(".", ","))
    df.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";")
    return caminho


if __name__ == "__main__":
    # Por enquanto, só a análise de compra única está ativa.
    # Quando quiser incluir a análise de cupom, troque para True e
    # preencha CUPOM_ALVO lá em cima.
    ANALISAR_CUPOM = False

    inicio_utc, fim_utc = janela_ultimos_meses(meses=6)
    subjanelas = gerar_subjanelas(inicio_utc, fim_utc, dias_por_janela=7)
    print(f"Buscando pedidos aprovados/faturados de {inicio_utc} até {fim_utc}, em {len(subjanelas)} janelas semanais...")

    todos_pedidos = []
    for status in STATUS_COMPRA_VALIDA:
        total_status = 0
        for sub_inicio, sub_fim in subjanelas:
            data_inicio_str = formatar_data_vtex(sub_inicio)
            data_fim_str = formatar_data_vtex(sub_fim, fim_do_intervalo=True)
            pedidos_status = buscar_pedidos_por_status(data_inicio_str, data_fim_str, status)
            todos_pedidos.extend(pedidos_status)
            total_status += len(pedidos_status)
        print(f"Status '{status}': {total_status} pedidos encontrados no total.")

    if not todos_pedidos:
        print("Nenhum pedido encontrado no período.")
    else:
        df_pedidos = pd.DataFrame(todos_pedidos).drop_duplicates(subset="orderId")
        print(f"Total de pedidos únicos: {len(df_pedidos)}. Iniciando enriquecimento (pode demorar)...")

        df_enriquecido = enriquecer_pedidos(df_pedidos)

        os.makedirs("analises", exist_ok=True)

        df_unicos = analisar_clientes_compra_unica(df_enriquecido)
        caminho_unicos = salvar_csv(df_unicos, "analises/clientes_compra_unica.csv")
        print(f"{len(df_unicos)} clientes com compra única. Salvo em: {caminho_unicos}")

        if ANALISAR_CUPOM:
            df_cupom = analisar_clientes_por_cupom(df_enriquecido, CUPOM_ALVO)
            caminho_cupom = salvar_csv(df_cupom, "analises/clientes_cupom.csv")
            print(f"{len(df_cupom)} pedidos com o cupom '{CUPOM_ALVO}'. Salvo em: {caminho_cupom}")