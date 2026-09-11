"""
consolidador_clientes.py
==========================================================================
Lê uma planilha bagunçada (CSV ou XLSX) — colunas em qualquer ordem, com
nomes diferentes, maiúsculas/minúsculas misturadas, com ou sem acento,
cabeçalho fora da primeira linha, múltiplas abas — e devolve uma planilha
limpa contendo apenas:

    Nome | Documento | Telefone | Email

Uso
----
    1. Coloque este script na mesma pasta da planilha (ou informe o
       caminho completo em ARQUIVO_ENTRADA).
    2. Edite APENAS a variável ARQUIVO_ENTRADA abaixo.
    3. Rode:  python consolidador_clientes.py
       (ou:   python consolidador_clientes.py "caminho\\da\\planilha.xlsx")

Dependências
------------
    pip install pandas openpyxl

Autor: Filipe (InfoStore) — gerado com apoio do Claude
==========================================================================
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

# ==========================================================================
# ÚNICA VARIÁVEL QUE PRECISA SER EDITADA
# ==========================================================================
ARQUIVO_ENTRADA = "clientes_compra_unica.csv"  # <-- troque pelo nome/caminho do seu arquivo
# ==========================================================================

# Se None, o arquivo de saída é gerado automaticamente como
# "<nome_do_arquivo>_consolidado.xlsx" na mesma pasta do arquivo de entrada.
ARQUIVO_SAIDA: str | None = None

# Quantidade de linhas iniciais varridas em busca do cabeçalho real
# (cobre casos de logotipo/título/linhas em branco antes da tabela).
LINHAS_VARREDURA_CABECALHO = 15

# ---------------------------------------------------------------------
# Apelidos possíveis para cada campo, escritos em palavras separadas por
# espaço. A comparação é feita por PALAVRA (token), não por igualdade
# exata do texto inteiro — assim "Nome do Cliente", "NOME_CLIENTE" e
# "Nome Completo do Comprador" são todos reconhecidos como o campo nome.
# ---------------------------------------------------------------------
ALIASES: dict[str, list[str]] = {
    "nome": [
        "nome", "cliente", "nome cliente", "nome completo", "client name",
        "name", "full name", "razao social", "responsavel", "comprador",
        "titular", "consumidor",
    ],
    "documento": [
        "documento", "cpf", "cnpj", "cpf cnpj", "document", "client document",
        "numero documento", "doc identidade", "identificacao", "rg",
    ],
    "telefone": [
        "telefone", "fone", "celular", "phone", "contato", "whatsapp",
        "numero telefone", "tel", "fone contato", "celular contato",
    ],
    "email": [
        "email", "e mail", "mail", "email cliente", "correio eletronico",
        "endereco email",
    ],
}

CAMPOS_FINAIS = ["Nome", "Documento", "Telefone", "Email"]


# ==========================================================================
# Normalização auxiliar
# ==========================================================================
def _tokenizar(valor: object) -> set[str]:
    """Minúsculo, sem acento, separado em palavras (tokens) — usado para comparar nomes de coluna
    de forma tolerante a espaços, pontuação, underline e ordem das palavras."""
    if valor is None:
        return set()
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    tokens = re.split(r"[^a-z0-9]+", texto)
    return {t for t in tokens if t}


def _titulo_inteligente(nome: str) -> str:
    """Capitaliza cada palavra, preservando siglas curtas em maiúsculo (ex.: 'ME', 'LTDA')."""
    palavras = str(nome).strip().split()
    resultado = []
    for p in palavras:
        if p.isupper() and len(p) <= 3:
            resultado.append(p)
        else:
            resultado.append(p.capitalize())
    return " ".join(resultado)


def _limpar_documento(valor: object) -> str:
    """Mantém apenas dígitos (CPF/CNPJ), preservando zeros à esquerda como texto."""
    if pd.isna(valor):
        return ""
    digitos = re.sub(r"\D", "", str(valor))
    return digitos


def _limpar_telefone(valor: object) -> str:
    """Mantém apenas dígitos e remove o DDI 55 quando presente (padrão BR)."""
    if pd.isna(valor):
        return ""
    digitos = re.sub(r"\D", "", str(valor))
    if digitos.startswith("55") and len(digitos) > 11:
        digitos = digitos[2:]
    return digitos


_REGEX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _limpar_email(valor: object) -> str:
    if pd.isna(valor):
        return ""
    email = str(valor).strip().lower()
    return email if _REGEX_EMAIL.match(email) else ""


# ==========================================================================
# Detecção de cabeçalho e mapeamento de colunas
# ==========================================================================
def _coluna_bate_com_campo(tokens_coluna: set[str], campo: str) -> bool:
    """Verdadeiro se algum alias do campo aparece inteiramente entre as palavras da coluna."""
    for alias in ALIASES[campo]:
        tokens_alias = _tokenizar(alias)
        if tokens_alias and tokens_alias.issubset(tokens_coluna):
            return True
    return False


def _encontrar_linha_cabecalho(bruto: pd.DataFrame) -> int | None:
    """
    Varre as primeiras N linhas de um DataFrame lido sem header e retorna
    o índice da linha com mais CAMPOS distintos reconhecidos — essa é a
    linha de cabeçalho real da planilha bagunçada (ignora título, logo,
    linhas em branco etc. que costumam vir antes da tabela).
    """
    melhor_linha, melhor_pontuacao = None, 0

    limite = min(LINHAS_VARREDURA_CABECALHO, len(bruto))
    for i in range(limite):
        campos_encontrados = _mapear_colunas(bruto.iloc[i].tolist())
        pontuacao = len(campos_encontrados)
        if pontuacao > melhor_pontuacao:
            melhor_pontuacao, melhor_linha = pontuacao, i

    # exige pelo menos 2 campos reconhecidos para considerar cabeçalho válido
    return melhor_linha if melhor_pontuacao >= 2 else None


def _mapear_colunas(colunas: list) -> dict[str, str]:
    """Retorna {'nome': 'Nome Coluna Original', 'documento': ..., ...} para os campos encontrados.
    Cada coluna só pode ser usada para um campo (a primeira correspondência encontrada, da
    esquerda para a direita, vence)."""
    mapa: dict[str, str] = {}
    colunas_usadas: set = set()
    for campo in ALIASES:
        for col_original in colunas:
            if col_original in colunas_usadas or pd.isna(col_original):
                continue
            tokens_coluna = _tokenizar(col_original)
            if _coluna_bate_com_campo(tokens_coluna, campo):
                mapa[campo] = col_original
                colunas_usadas.add(col_original)
                break
    return mapa


# ==========================================================================
# Leitura robusta (CSV ou XLSX, com detecção de cabeçalho por aba)
# ==========================================================================
def _ler_planilha_bruta(caminho: Path) -> dict[str, pd.DataFrame]:
    """Retorna {nome_da_aba: DataFrame_sem_header}. Para CSV, a 'aba' é o próprio arquivo."""
    sufixo = caminho.suffix.lower()

    if sufixo == ".csv":
        for encoding in ("utf-8-sig", "latin1", "cp1252"):
            for separador in (";", ",", "\t"):
                try:
                    df = pd.read_csv(
                        caminho, header=None, sep=separador,
                        encoding=encoding, engine="python", dtype=str,
                    )
                    if df.shape[1] > 1:  # separador correto encontrado
                        return {caminho.stem: df}
                except Exception:
                    continue
        raise ValueError(f"Não foi possível ler o CSV '{caminho.name}' (encoding/separador não identificados).")

    elif sufixo in (".xlsx", ".xls", ".xlsm"):
        abas = pd.read_excel(caminho, sheet_name=None, header=None, dtype=str, engine="openpyxl")
        return abas

    else:
        raise ValueError(f"Formato não suportado: '{sufixo}'. Use .csv, .xlsx, .xls ou .xlsm.")


def _processar_aba(bruto: pd.DataFrame, origem: str) -> pd.DataFrame:
    """Detecta cabeçalho, mapeia colunas, limpa valores. Retorna DataFrame só com CAMPOS_FINAIS (pode vir vazio)."""
    linha_cabecalho = _encontrar_linha_cabecalho(bruto)
    if linha_cabecalho is None:
        print(f"  [aviso] '{origem}': nenhum cabeçalho reconhecível encontrado — aba ignorada.")
        return pd.DataFrame(columns=CAMPOS_FINAIS)

    cabecalho = bruto.iloc[linha_cabecalho].tolist()
    dados = bruto.iloc[linha_cabecalho + 1:].copy()
    dados.columns = cabecalho

    mapa = _mapear_colunas(list(dados.columns))
    faltando = [c for c in ALIASES if c not in mapa]
    if faltando:
        print(f"  [aviso] '{origem}': campo(s) não encontrado(s) -> {', '.join(faltando)}")

    saida = pd.DataFrame()
    saida["Nome"] = dados[mapa["nome"]].apply(lambda v: _titulo_inteligente(v) if pd.notna(v) else "") if "nome" in mapa else ""
    saida["Documento"] = dados[mapa["documento"]].apply(_limpar_documento) if "documento" in mapa else ""
    saida["Telefone"] = dados[mapa["telefone"]].apply(_limpar_telefone) if "telefone" in mapa else ""
    saida["Email"] = dados[mapa["email"]].apply(_limpar_email) if "email" in mapa else ""

    # descarta linhas totalmente vazias/em branco (rodapés, linhas separadoras etc.)
    saida = saida[(saida != "").any(axis=1)]
    return saida.reset_index(drop=True)


# ==========================================================================
# Consolidação / deduplicação
# ==========================================================================
def _consolidar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa por Documento (ou, na ausência dele, por Email) e mantém, para
    cada campo, o valor mais completo (mais longo) entre as linhas duplicadas.
    """
    df = df.copy()
    df["_chave"] = df["Documento"].where(df["Documento"] != "", df["Email"])
    df = df[df["_chave"] != ""]  # sem documento e sem email não há como identificar o cliente

    def _mais_completo(serie: pd.Series) -> str:
        valores = [v for v in serie if isinstance(v, str) and v.strip() != ""]
        return max(valores, key=len) if valores else ""

    consolidado = (
        df.groupby("_chave", as_index=False)
        .agg({
            "Nome": _mais_completo,
            "Documento": _mais_completo,
            "Telefone": _mais_completo,
            "Email": _mais_completo,
        })
    )
    return consolidado[CAMPOS_FINAIS].sort_values("Nome").reset_index(drop=True)


# ==========================================================================
# Saída formatada (.xlsx)
# ==========================================================================
def _salvar_xlsx(df: pd.DataFrame, caminho_saida: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Clientes"

    ws.append(CAMPOS_FINAIS)
    preenchimento = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    fonte_cabecalho = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    borda = Border(*(Side(style="thin", color="D9D9D9"),) * 4)

    for celula in ws[1]:
        celula.fill = preenchimento
        celula.font = fonte_cabecalho
        celula.alignment = Alignment(horizontal="center", vertical="center")
        celula.border = borda
    ws.row_dimensions[1].height = 22

    for _, linha in df.iterrows():
        ws.append(list(linha))

    for linha in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=4):
        for celula in linha:
            celula.font = Font(name="Arial", size=10)
            celula.border = borda
            celula.alignment = Alignment(horizontal="left", vertical="center")
        linha[1].number_format = "@"  # Documento como texto (preserva zeros à esquerda)
        linha[2].number_format = "@"  # Telefone como texto

    larguras = {"A": 32, "B": 18, "C": 16, "D": 32}
    for coluna, largura in larguras.items():
        ws.column_dimensions[coluna].width = largura

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:D{ws.max_row}"

    wb.save(caminho_saida)


# ==========================================================================
# Execução principal
# ==========================================================================
def main() -> None:
    entrada = sys.argv[1] if len(sys.argv) > 1 else ARQUIVO_ENTRADA
    caminho_entrada = Path(entrada)

    if not caminho_entrada.exists():
        print(f"Erro: arquivo não encontrado -> {caminho_entrada}")
        sys.exit(1)

    print(f"Lendo '{caminho_entrada.name}'...")
    abas = _ler_planilha_bruta(caminho_entrada)

    partes = []
    for nome_aba, bruto in abas.items():
        print(f"Processando aba/arquivo: {nome_aba}")
        partes.append(_processar_aba(bruto, nome_aba))

    bruto_total = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=CAMPOS_FINAIS)
    if bruto_total.empty:
        print("Nenhum dado reconhecível foi encontrado na planilha.")
        sys.exit(1)

    print(f"Linhas brutas reconhecidas: {len(bruto_total)}")
    consolidado = _consolidar(bruto_total)
    print(f"Clientes únicos após consolidação: {len(consolidado)}")

    caminho_saida = Path(ARQUIVO_SAIDA) if ARQUIVO_SAIDA else caminho_entrada.with_name(
        f"{caminho_entrada.stem}_consolidado.xlsx"
    )
    _salvar_xlsx(consolidado, caminho_saida)
    print(f"Arquivo gerado: {caminho_saida}")


if __name__ == "__main__":
    main()