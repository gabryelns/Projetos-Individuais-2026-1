"""
Crawler de Monitoramento - Centrais de Resultados (RI)
======================================================
Responsável por:
- Monitorar periodicamente os sites de RI das construtoras
- Detectar novos PDFs de Prévias Operacionais / Boletins
- Calcular hash SHA-256 para garantir idempotência
- Disparar o pipeline de ingestão quando um novo PDF é detectado

Estratégia de Gatilho: POLLING / CRONJOB
Justificativa: Os sites de RI das construtoras não disponibilizam webhooks
ou feeds RSS padronizados. O polling agendado (ex: 1x/dia) é a abordagem
mais compatível e não sobrecarrega os servidores das empresas.
"""

import os
import sys
import time
import hashlib
import requests
import schedule
from datetime import datetime
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db.database import (
    documento_ja_processado,
    registrar_documento,
    marcar_processado,
    marcar_erro,
    salvar_dados_boletim,
    calcular_hash_url
)
from extractor.pdf_extractor import processar_pdf


# ─── Configuração das Fontes ──────────────────────────────────────────────────
# Mapeamento das centrais de resultados das principais construtoras.
# Cada entrada define onde buscar e quais palavras-chave identificam
# uma Prévia Operacional ou Boletim de Conjuntura.
FONTES_RI = [
    {
        "empresa": "Ministério das Cidades",
        "url_ri": "https://www.gov.br/cidades/pt-br",
        "palavras_chave_pdf": ["boletim", "conjuntura", "habitacional", "prévia", "operacional"],
        "ativo": True
    },
    {
        "empresa": "MRV",
        "url_ri": "https://ri.mrv.com.br/central-de-resultados",
        "palavras_chave_pdf": ["prévia", "operacional", "resultados", "3t", "2t", "1t", "4t"],
        "ativo": True
    },
    {
        "empresa": "Cury",
        "url_ri": "https://ri.cury.com.br/central-de-resultados",
        "palavras_chave_pdf": ["prévia", "operacional", "release", "resultados"],
        "ativo": True
    },
    {
        "empresa": "Tenda",
        "url_ri": "https://ri.construtora-tenda.com/central-de-resultados",
        "palavras_chave_pdf": ["prévia", "operacional", "resultados", "release"],
        "ativo": True
    },
    {
        "empresa": "Plano & Plano",
        "url_ri": "https://ri.planoplano.com.br/central-de-resultados",
        "palavras_chave_pdf": ["prévia", "operacional", "resultados"],
        "ativo": True
    },
    {
        "empresa": "Direcional",
        "url_ri": "https://ri.direcional.com.br/central-de-resultados",
        "palavras_chave_pdf": ["prévia", "operacional", "resultados", "release"],
        "ativo": True
    },
    {
        "empresa": "Pacaembu",
        "url_ri": "https://ri.pacaembu.com/central-de-resultados",
        "palavras_chave_pdf": ["prévia", "operacional", "resultados"],
        "ativo": True
    },
]

# Headers para simular navegador real e evitar bloqueios
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "pdfs")


def normalizar_url(url: str, base_url: str) -> str:
    """Converte URLs relativas em absolutas."""
    if url.startswith("http"):
        return url
    return urljoin(base_url, url)


def descobrir_links_pdf(url_ri: str, palavras_chave: list[str]) -> list[dict]:
    """
    Varre a página de RI de uma construtora buscando links de PDF
    que correspondam às palavras-chave configuradas.
    Retorna lista de dicts com {url, nome_arquivo}.
    """
    pdfs_encontrados = []

    try:
        resposta = requests.get(url_ri, headers=HEADERS, timeout=15)
        resposta.raise_for_status()
    except requests.RequestException as e:
        print(f"[CRAWLER] ⚠ Falha ao acessar {url_ri}: {e}")
        return []

    soup = BeautifulSoup(resposta.text, "html.parser")

    for tag_a in soup.find_all("a", href=True):
        href = tag_a["href"].lower()
        texto_link = tag_a.get_text(strip=True).lower()

        # Verifica se é um PDF
        if not (href.endswith(".pdf") or "pdf" in href):
            continue

        # Verifica se o link ou texto contém palavras-chave relevantes
        conteudo_verificavel = href + " " + texto_link
        if any(kw in conteudo_verificavel for kw in palavras_chave):
            url_absoluta = normalizar_url(tag_a["href"], url_ri)
            nome = os.path.basename(urlparse(url_absoluta).path) or "documento.pdf"
            pdfs_encontrados.append({
                "url": url_absoluta,
                "nome_arquivo": nome
            })

    print(f"[CRAWLER] {len(pdfs_encontrados)} PDF(s) relevante(s) encontrado(s) em {url_ri}")
    return pdfs_encontrados


def calcular_hash_conteudo_url(url: str) -> str:
    """
    Baixa apenas os primeiros 64KB do PDF para calcular um hash preliminar
    sem consumir banda desnecessária. Útil para triagem rápida.
    Se já existe no catálogo, evita download completo.
    """
    try:
        resp = requests.get(url, headers=HEADERS, stream=True, timeout=15)
        resp.raise_for_status()
        primeiros_bytes = b""
        for chunk in resp.iter_content(chunk_size=8192):
            primeiros_bytes += chunk
            if len(primeiros_bytes) >= 65536:
                break
        return hashlib.sha256(primeiros_bytes).hexdigest()
    except Exception:
        # Fallback: usa hash da URL se não conseguir baixar
        return calcular_hash_url(url)


def baixar_pdf(url: str, nome_arquivo: str) -> str:
    """
    Faz o download completo de um PDF e salva em disco.
    Retorna o caminho local do arquivo salvo.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    caminho_local = os.path.join(DOWNLOAD_DIR, nome_arquivo)

    print(f"[CRAWLER] Baixando: {url}")
    resposta = requests.get(url, headers=HEADERS, timeout=30)
    resposta.raise_for_status()

    with open(caminho_local, "wb") as f:
        f.write(resposta.content)

    print(f"[CRAWLER] ✓ PDF salvo em: {caminho_local} ({len(resposta.content) / 1024:.1f} KB)")
    return caminho_local


def processar_fonte(fonte: dict):
    """
    Processa uma única fonte de RI:
    1. Descobre links de PDF
    2. Para cada PDF: verifica idempotência via hash
    3. Se novo: baixa, processa com LLM e persiste no banco
    """
    empresa = fonte["empresa"]
    print(f"\n{'='*50}")
    print(f"[CRAWLER] Verificando: {empresa}")
    print(f"{'='*50}")

    pdfs = descobrir_links_pdf(fonte["url_ri"], fonte["palavras_chave_pdf"])

    for pdf_info in pdfs:
        url_pdf = pdf_info["url"]
        nome_arquivo = pdf_info["nome_arquivo"]

        # ── IDEMPOTÊNCIA: verificação rápida via hash da URL ────────────────
        # Antes de baixar o arquivo completo, checa se a URL já foi processada
        hash_preliminar = calcular_hash_url(url_pdf)
        if documento_ja_processado(hash_preliminar):
            continue  # Já processado: ignora sem gastar banda ou tokens

        # ── Download completo do PDF ────────────────────────────────────────
        try:
            caminho_local = baixar_pdf(url_pdf, nome_arquivo)
        except Exception as e:
            print(f"[CRAWLER] ✗ Falha no download de {url_pdf}: {e}")
            continue

        # ── Hash do conteúdo real do arquivo (SHA-256 completo) ─────────────
        from db.database import calcular_hash_arquivo
        hash_real = calcular_hash_arquivo(caminho_local)

        if documento_ja_processado(hash_real):
            continue  # Mesmo conteúdo com URL diferente: ignora

        # ── Registro no Catálogo (linhagem) ────────────────────────────────
        doc_id = registrar_documento(
            hash_sha256=hash_real,
            url_origem=url_pdf,
            nome_arquivo=nome_arquivo,
            empresa_fonte=empresa
        )

        # ── Processamento LLM ───────────────────────────────────────────────
        try:
            boletim = processar_pdf(caminho_local)
            salvar_dados_boletim(doc_id, boletim)
            marcar_processado(doc_id, boletim.ano, boletim.trimestre)
            print(f"[PIPELINE] ✓ {empresa} | {boletim.ano}/T{boletim.trimestre} processado com sucesso!")

        except Exception as e:
            mensagem_erro = f"{type(e).__name__}: {str(e)}"
            marcar_erro(doc_id, mensagem_erro)
            print(f"[PIPELINE] ✗ Erro ao processar {nome_arquivo}: {mensagem_erro}")


def processar_pdf_local(caminho_pdf: str, empresa_fonte: str = "Manual", url_origem: str = "local://manual"):
    """
    Processa um PDF já disponível localmente (sem necessidade de crawling).
    Útil para ingestão manual do Boletim de Conjuntura ou testes.
    """
    from db.database import calcular_hash_arquivo

    print(f"\n[MANUAL] Processando PDF local: {caminho_pdf}")

    hash_real = calcular_hash_arquivo(caminho_pdf)

    if documento_ja_processado(hash_real):
        print("[MANUAL] Documento já foi processado anteriormente. Nada a fazer.")
        return

    nome_arquivo = os.path.basename(caminho_pdf)
    doc_id = registrar_documento(
        hash_sha256=hash_real,
        url_origem=url_origem,
        nome_arquivo=nome_arquivo,
        empresa_fonte=empresa_fonte
    )

    try:
        boletim = processar_pdf(caminho_pdf)
        salvar_dados_boletim(doc_id, boletim)
        marcar_processado(doc_id, boletim.ano, boletim.trimestre)
        print(f"[MANUAL] ✓ Processamento concluído: {boletim.ano}/T{boletim.trimestre}")
        return boletim
    except Exception as e:
        mensagem_erro = f"{type(e).__name__}: {str(e)}"
        marcar_erro(doc_id, mensagem_erro)
        print(f"[MANUAL] ✗ Erro: {mensagem_erro}")
        raise


def executar_varredura_completa():
    """Executa uma varredura em todas as fontes ativas."""
    print(f"\n[SCHEDULER] Iniciando varredura — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for fonte in FONTES_RI:
        if fonte.get("ativo", False):
            processar_fonte(fonte)
    print(f"\n[SCHEDULER] Varredura concluída — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def iniciar_monitoramento_continuo(intervalo_horas: int = 24):
    """
    Inicia o loop de monitoramento contínuo usando agendamento (polling).
    Por padrão, varre todas as fontes uma vez por dia.
    Ajuste o intervalo_horas conforme necessário.
    """
    print(f"[SCHEDULER] Monitoramento iniciado. Intervalo: a cada {intervalo_horas}h")
    print("[SCHEDULER] Executando varredura inicial imediata...")

    # Varredura imediata ao iniciar
    executar_varredura_completa()

    # Agendamento periódico
    schedule.every(intervalo_horas).hours.do(executar_varredura_completa)

    print(f"[SCHEDULER] Próxima varredura agendada em {intervalo_horas}h. Aguardando...")
    while True:
        schedule.run_pending()
        time.sleep(60)  # Checa o agendamento a cada minuto


if __name__ == "__main__":
    iniciar_monitoramento_continuo(intervalo_horas=24)
