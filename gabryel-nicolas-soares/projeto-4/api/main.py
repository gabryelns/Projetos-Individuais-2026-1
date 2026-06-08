"""
Camada de Serviço - API REST (FastAPI)
======================================
Disponibiliza os dados extraídos via endpoints REST/JSON.
Permite filtrar por empresa, ano e trimestre.

Endpoints:
  GET  /api/conjuntura               → lista todos os registros (com filtros opcionais)
  GET  /api/conjuntura/{empresa}     → dados de uma empresa específica
  GET  /api/catalogo                 → catálogo de documentos com linhagem completa
  GET  /api/catalogo/{hash}          → detalhe de um documento pelo hash SHA-256
  POST /api/ingerir                  → dispara ingestão de um PDF local ou por URL
  GET  /health                       → health check
"""

import os
import sys
import sqlite3
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db.database import DB_PATH, get_conn, init_db


app = FastAPI(
    title="Pipeline UDA — Conjuntura do Setor Habitacional",
    description=(
        "API de consulta aos dados operacionais extraídos automaticamente "
        "das Centrais de Resultados (RI) das construtoras brasileiras."
    ),
    version="1.0.0",
)


# ─── Schemas de Resposta ──────────────────────────────────────────────────────

class DadosEmpresa(BaseModel):
    empresa: str
    ano: int
    trimestre: int
    # Lançamentos
    lanc_vs_tri_anterior: Optional[float]
    lanc_vs_mesmo_tri_ano_ant: Optional[float]
    lanc_acum_9m_ano_ant: Optional[float]
    lanc_acum_9m_atual: Optional[float]
    # Vendas
    vend_vs_tri_anterior: Optional[float]
    vend_vs_mesmo_tri_ano_ant: Optional[float]
    vend_acum_9m_ano_ant: Optional[float]
    vend_acum_9m_atual: Optional[float]
    # Linhagem
    url_origem: Optional[str]
    hash_documento: Optional[str]
    data_processamento: Optional[str]


class TotaisSetor(BaseModel):
    ano: int
    trimestre: int
    total_lanc_vs_tri_anterior: Optional[float]
    total_lanc_vs_mesmo_tri_ano_ant: Optional[float]
    total_lanc_acum_9m_ano_ant: Optional[float]
    total_lanc_acum_9m_atual: Optional[float]
    total_vend_vs_tri_anterior: Optional[float]
    total_vend_vs_mesmo_tri_ano_ant: Optional[float]
    total_vend_acum_9m_ano_ant: Optional[float]
    total_vend_acum_9m_atual: Optional[float]
    observacao_editorial: Optional[str]


class DocumentoCatalogo(BaseModel):
    id: int
    hash_sha256: str
    url_origem: str
    nome_arquivo: str
    empresa_fonte: str
    data_coleta: str
    data_processamento: Optional[str]
    status: str
    erro_mensagem: Optional[str]
    ano_referencia: Optional[int]
    trimestre_referencia: Optional[int]


class IngerirRequest(BaseModel):
    caminho_pdf: Optional[str] = None
    url_pdf: Optional[str] = None
    empresa_fonte: str = "Manual"


# ─── Inicialização ────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    print("[API] Banco de dados inicializado.")


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    """Verifica se a API está operacional."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get(
    "/api/conjuntura",
    response_model=list[DadosEmpresa],
    summary="Lista dados de conjuntura com filtros opcionais"
)
def listar_conjuntura(
    empresa: Optional[str] = Query(None, description="Nome da empresa (ex: MRV, Cury)"),
    ano: Optional[int] = Query(None, description="Ano de referência (ex: 2025)"),
    trimestre: Optional[int] = Query(None, ge=1, le=4, description="Trimestre (1 a 4)")
):
    """
    Retorna os dados de lançamentos e vendas extraídos dos boletins.
    Cada registro inclui a linhagem do documento de origem (URL + hash).

    Exemplos:
    - /api/conjuntura?empresa=MRV&ano=2025&trimestre=3
    - /api/conjuntura?ano=2025
    - /api/conjuntura?empresa=Cury
    """
    query = """
        SELECT
            b.empresa, b.ano, b.trimestre,
            b.lanc_vs_tri_anterior, b.lanc_vs_mesmo_tri_ano_ant,
            b.lanc_acum_9m_ano_ant, b.lanc_acum_9m_atual,
            b.vend_vs_tri_anterior, b.vend_vs_mesmo_tri_ano_ant,
            b.vend_acum_9m_ano_ant, b.vend_acum_9m_atual,
            c.url_origem, c.hash_sha256 AS hash_documento, c.data_processamento
        FROM boletim_conjuntura b
        JOIN catalogo_documentos c ON b.fk_documento_id = c.id
        WHERE 1=1
    """
    params = []

    if empresa:
        query += " AND LOWER(b.empresa) LIKE LOWER(?)"
        params.append(f"%{empresa}%")
    if ano:
        query += " AND b.ano = ?"
        params.append(ano)
    if trimestre:
        query += " AND b.trimestre = ?"
        params.append(trimestre)

    query += " ORDER BY b.ano DESC, b.trimestre DESC, b.empresa ASC"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Nenhum dado encontrado para os filtros informados."
        )

    return [dict(row) for row in rows]


@app.get(
    "/api/conjuntura/totais",
    response_model=list[TotaisSetor],
    summary="Totais agregados do setor por período"
)
def listar_totais_setor(
    ano: Optional[int] = Query(None),
    trimestre: Optional[int] = Query(None, ge=1, le=4)
):
    """Retorna os totais do setor habitacional filtrados por ano/trimestre."""
    query = "SELECT * FROM totais_setor WHERE 1=1"
    params = []

    if ano:
        query += " AND ano = ?"
        params.append(ano)
    if trimestre:
        query += " AND trimestre = ?"
        params.append(trimestre)

    query += " ORDER BY ano DESC, trimestre DESC"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="Nenhum total encontrado.")

    return [dict(row) for row in rows]


@app.get(
    "/api/catalogo",
    response_model=list[DocumentoCatalogo],
    summary="Catálogo de documentos com linhagem completa"
)
def listar_catalogo(
    status: Optional[str] = Query(None, description="Filtrar por status: pendente | processado | erro"),
    empresa: Optional[str] = Query(None)
):
    """
    Lista o catálogo de todos os documentos coletados.
    Inclui linhagem completa: URL de origem, hash SHA-256, timestamps e status.
    """
    query = "SELECT * FROM catalogo_documentos WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)
    if empresa:
        query += " AND LOWER(empresa_fonte) LIKE LOWER(?)"
        params.append(f"%{empresa}%")

    query += " ORDER BY data_coleta DESC"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]


@app.get(
    "/api/catalogo/{hash_sha256}",
    response_model=DocumentoCatalogo,
    summary="Detalhe de um documento pelo hash SHA-256"
)
def detalhe_documento(hash_sha256: str):
    """Retorna os metadados de linhagem de um documento específico pelo hash."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM catalogo_documentos WHERE hash_sha256 = ?",
            (hash_sha256,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Documento não encontrado no catálogo.")

    return dict(row)


@app.post(
    "/api/ingerir",
    summary="Dispara ingestão manual de um PDF"
)
def ingerir_pdf(request: IngerirRequest, background_tasks: BackgroundTasks):
    """
    Dispara o processamento de um PDF em background.
    Aceita caminho local ou URL pública do PDF.
    """
    from crawler.ri_crawler import processar_pdf_local

    if not request.caminho_pdf and not request.url_pdf:
        raise HTTPException(
            status_code=400,
            detail="Informe 'caminho_pdf' (local) ou 'url_pdf' (remoto)."
        )

    def _ingerir():
        if request.caminho_pdf:
            processar_pdf_local(
                caminho_pdf=request.caminho_pdf,
                empresa_fonte=request.empresa_fonte,
                url_origem=f"local://{request.caminho_pdf}"
            )
        else:
            # Download + processamento via URL
            import requests as req
            import tempfile
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = req.get(request.url_pdf, headers=headers, timeout=30)
            resp.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name
            processar_pdf_local(
                caminho_pdf=tmp_path,
                empresa_fonte=request.empresa_fonte,
                url_origem=request.url_pdf
            )

    background_tasks.add_task(_ingerir)

    return {
        "status": "aceito",
        "mensagem": "Processamento iniciado em background.",
        "origem": request.caminho_pdf or request.url_pdf
    }