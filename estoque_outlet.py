"""
extrair_outlet_candidatos.py

Extrai uma LISTA (não automática) de produtos candidatos à sessão Outlet,
para curadoria manual posterior no Admin VTEX (Catálogo > Coleções).

Escopo: TODO o catálogo ativo (não se restringe a categorias específicas).

Regras aplicadas:
  1. Produto precisa estar categorizado (Marca preenchida + pelo menos
     uma Categoria/Subcategoria associada).
  2. Saldo disponível (Total - Reservado) SOMADO apenas das filiais
     TARGET_WAREHOUSES (por padrão INF50 e INF01) deve ser:
         MIN_STOCK <= saldo < MAX_STOCK_EXCLUSIVE
     ou seja, com os padrões abaixo: saldo igual a 1 ou 2 unidades.

Saída: uma planilha .xlsx ordenada por maior % de desconto primeiro,
com uma linha por SKU elegível.

Nada aqui grava, publica ou altera qualquer coisa no VTEX — é somente leitura.
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

# ============ CONFIGURAÇÃO ============

# IDs técnicos dos depósitos (Admin > Envio > Estratégia de envio > Depósitos)
TARGET_WAREHOUSES = ["INF50", "INF01"]

MIN_STOCK = 1
MAX_STOCK = 3  # inclusive: aceita saldo 1, 2 ou 3

# Ignora SKUs com preço de lista abaixo deste valor. 0 = desativado.
MIN_PRICE = 0

SKU_PAGE_SIZE = 1000
MAX_WORKERS = 10
RATE_LIMIT_BACKOFF_SECONDS = 60

# ---- MODO DE TESTE ----
# Com TEST_MODE = True, o script roda só numa amostra pequena do catálogo
# (as primeiras TEST_SAMPLE_SIZE SKUs retornadas pela API), pra validar
# rapidamente se tudo está funcionando antes de rodar o catálogo inteiro.
TEST_MODE = False
TEST_SAMPLE_SIZE = 300

VTEX_ACCOUNT = os.getenv("VTEX_ACCOUNT_NAME")
VTEX_ENVIRONMENT = os.getenv("VTEX_ENVIRONMENT", "vtexcommercestable")
VTEX_APPKEY = os.getenv("VTEX_APP_KEY")
VTEX_APPTOKEN = os.getenv("VTEX_APP_TOKEN")

BASE_URL = f"https://{VTEX_ACCOUNT}.{VTEX_ENVIRONMENT}.com.br"

HEADERS = {
    "X-VTEX-API-AppKey": VTEX_APPKEY,
    "X-VTEX-API-AppToken": VTEX_APPTOKEN,
    "Accept": "application/json",
}


def requisitar(url, params=None, tentativas=5):
    """GET com tratamento de rate limit (429) e pequenas retentativas."""
    resp = None
    for tentativa in range(tentativas):
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)

        if resp.status_code == 429:
            print(f"  [RATE LIMIT] Aguardando {RATE_LIMIT_BACKOFF_SECONDS}s...")
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
            continue

        if resp.status_code in (200, 206):
            return resp

        if tentativa < tentativas - 1:
            time.sleep(2)
            continue

        return resp

    return resp


def obter_todos_os_sku_ids():
    """Pagina /catalog_system/pvt/sku/stockkeepingunitids até esgotar os SKUs ativos."""
    sku_ids = []
    pagina = 1

    while True:
        url = f"{BASE_URL}/api/catalog_system/pvt/sku/stockkeepingunitids"
        params = {"page": pagina, "pagesize": SKU_PAGE_SIZE}
        resp = requisitar(url, params=params)

        if resp is None or resp.status_code not in (200, 206):
            status = resp.status_code if resp is not None else "sem resposta"
            print(f"[ERRO] Falha ao listar SKUs (página {pagina}): status {status}")
            break

        lote = resp.json()
        if not lote:
            break

        sku_ids.extend(lote)
        print(f"  {len(sku_ids)} SKU IDs coletados até agora...")

        if len(lote) < SKU_PAGE_SIZE:
            break

        pagina += 1

    return sku_ids


def calcular_saldo_filial(sku_id):
    """Consulta o inventário do SKU e soma o saldo disponível apenas
    das filiais em TARGET_WAREHOUSES. Retorna None se não atender às regras."""
    url = f"{BASE_URL}/api/logistics/pvt/inventory/skus/{sku_id}"
    resp = requisitar(url)

    if resp is None or resp.status_code != 200:
        return None

    dados = resp.json()
    saldo_total = 0
    encontrou_filial = False

    for deposito in dados.get("balance", []):
        if deposito.get("warehouseId") not in TARGET_WAREHOUSES:
            continue
        if deposito.get("hasUnlimitedQuantity"):
            # Estoque "infinito" não se encaixa no conceito de outlet por saldo baixo
            return None

        encontrou_filial = True
        total = deposito.get("totalQuantity", 0) or 0
        reservado = deposito.get("reservedQuantity", 0) or 0
        saldo_total += max(total - reservado, 0)

    if not encontrou_filial:
        return None

    if MIN_STOCK <= saldo_total <= MAX_STOCK:
        return saldo_total

    return None


def obter_sku_master(sku_id):
    """Dados cadastrais básicos do SKU: productId, nome, se está ativo."""
    url = f"{BASE_URL}/api/catalog_system/pvt/sku/stockkeepingunitbyid/{sku_id}"
    resp = requisitar(url)
    if resp is None or resp.status_code != 200:
        return None
    return resp.json()


def obter_detalhes_produto(product_id):
    """Marca, caminho de categoria, e itens (com preço/desconto) do produto."""
    url = f"{BASE_URL}/api/catalog_system/pub/products/search"
    params = {"fq": f"productId:{product_id}"}
    resp = requisitar(url, params=params)
    if resp is None or resp.status_code not in (200, 206):
        return None
    resultado = resp.json()
    return resultado[0] if resultado else None


def esta_categorizado(produto):
    marca = (produto.get("brand") or "").strip()
    categorias = produto.get("categories", [])
    return bool(marca) and bool(categorias)


def extrair_caminho_categoria(produto):
    categorias = produto.get("categories", [])
    if not categorias:
        return ""
    return categorias[0].strip("/").replace("/", " > ")


def processar_sku(sku_id):
    """Pipeline completo para um único SKU. Retorna a linha de saída ou None."""
    saldo_filial = calcular_saldo_filial(sku_id)
    if saldo_filial is None:
        return None

    master = obter_sku_master(sku_id)
    if not master or not master.get("IsActive", False):
        return None

    product_id = master.get("ProductId")
    if not product_id:
        return None

    produto = obter_detalhes_produto(product_id)
    if not produto or not esta_categorizado(produto):
        return None

    # localiza o item correspondente a este SKU dentro do produto
    item_correspondente = None
    for item in produto.get("items", []):
        if str(item.get("itemId")) == str(sku_id):
            item_correspondente = item
            break

    if not item_correspondente:
        return None

    sellers = item_correspondente.get("sellers", [])
    if not sellers:
        return None

    oferta = sellers[0].get("commertialOffer", {})
    preco = oferta.get("Price")
    preco_lista = oferta.get("ListPrice")

    if MIN_PRICE and preco_lista is not None and preco_lista < MIN_PRICE:
        return None

    desconto_pct = 0.0
    if preco_lista and preco_lista > 0 and preco is not None:
        desconto_pct = round((1 - (preco / preco_lista)) * 100, 1)

    return {
        "Marca": (produto.get("brand") or "").strip(),
        "CaminhoCategoria": extrair_caminho_categoria(produto),
        "ProductId": product_id,
        "SkuId": sku_id,
        "NomeProduto": produto.get("productName", ""),
        "NomeSKU": item_correspondente.get("nameComplete", ""),
        "SaldoFilialAlvo": saldo_filial,
        "PrecoListaR$": preco_lista,
        "PrecoAtualR$": preco,
        "DescontoPct": desconto_pct,
        "Link": produto.get("link", ""),
    }


def main():
    if not all([VTEX_ACCOUNT, VTEX_APPKEY, VTEX_APPTOKEN]):
        print("Faltam variáveis no .env: VTEX_ACCOUNT_NAME, VTEX_APP_KEY, VTEX_APP_TOKEN.")
        sys.exit(1)

    print("Etapa 1/2: coletando todos os SKU IDs ativos do catálogo...")
    sku_ids = obter_todos_os_sku_ids()
    print(f"Total de SKUs no catálogo: {len(sku_ids)}\n")

    if not sku_ids:
        print("Nenhum SKU retornado. Verifique as credenciais e permissões da API Key.")
        return

    if TEST_MODE:
        sku_ids = sku_ids[:TEST_SAMPLE_SIZE]
        print(
            f"[MODO DE TESTE ATIVO] Rodando apenas com uma amostra de "
            f"{len(sku_ids)} SKUs. Ajuste TEST_MODE = False no topo do "
            f"script pra rodar o catálogo inteiro.\n"
        )

    print(f"Etapa 2/2: verificando saldo em {TARGET_WAREHOUSES} para cada SKU "
          f"({MAX_WORKERS} threads)...\n")

    linhas = []
    processados = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futuros = {executor.submit(processar_sku, sku_id): sku_id for sku_id in sku_ids}

        for futuro in as_completed(futuros):
            processados += 1
            if processados % 500 == 0:
                print(f"  {processados}/{len(sku_ids)} SKUs verificados...")

            resultado = futuro.result()
            if resultado:
                linhas.append(resultado)

    if not linhas:
        print(
            "\nNenhum produto atendeu às regras (categorizado + saldo entre "
            f"{MIN_STOCK} e {MAX_STOCK} nas filiais {TARGET_WAREHOUSES})."
        )
        if TEST_MODE:
            print(
                "Isso é esperado em uma amostra pequena (TEST_MODE) — tente "
                "aumentar TEST_SAMPLE_SIZE ou rodar o catálogo completo."
            )
        return

    df = pd.DataFrame(linhas)
    df = df.drop_duplicates(subset=["SkuId"])
    df = df.sort_values(by="DescontoPct", ascending=False)

    media_preco = df["PrecoAtualR$"].mean()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    sufixo_teste = "_TESTE" if TEST_MODE else ""
    nome_arquivo = f"outlet_candidatos{sufixo_teste}_{timestamp}.xlsx"
    df.to_excel(nome_arquivo, index=False)

    print(f"\n{len(df)} SKUs candidatos a Outlet exportados para: {nome_arquivo}")
    print(f"Preço médio dos candidatos: R$ {media_preco:,.2f}")


if __name__ == "__main__":
    main()