"""
Camada de Persistência - SQLite + Catálogo de Linhagem
=======================================================
Responsável por:
- Criar e manter o banco de dados SQLite
- Registrar linhagem (data lineage) de cada PDF processado
- Verificar idempotência via hash SHA-256 (evitar reprocessamento)
- Persistir dados extraídos pelo LLM
"""

import sqlite3
import hashlib
import os
from datetime import datetime
from typing import Optional
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "conjuntura.db")


def init_db():
    """Inicializa o banco de dados criando as tabelas necessárias."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            -- Catálogo de Dados e Linhagem
            -- Registra cada PDF coletado e seu status de processamento
            CREATE TABLE IF NOT EXISTS catalogo_documentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash_sha256 TEXT UNIQUE NOT NULL,       -- Fingerprint do arquivo (idempotência)
                url_origem TEXT NOT NULL,               -- Link do PDF no site de RI
                nome_arquivo TEXT NOT NULL,             -- Nome do arquivo
                empresa_fonte TEXT NOT NULL,            -- Empresa que publicou
                data_coleta TEXT NOT NULL,              -- Timestamp da coleta
                data_processamento TEXT,                -- Timestamp do processamento LLM
                status TEXT DEFAULT 'pendente',         -- pendente | processado | erro
                erro_mensagem TEXT,                     -- Detalhe do erro se houver
                ano_referencia INTEGER,                 -- Ano dos dados
                trimestre_referencia INTEGER            -- Trimestre dos dados
            );

            -- Dados principais extraídos por LLM
            -- Cada linha tem rastreabilidade ao documento de origem via fk_documento_id
            CREATE TABLE IF NOT EXISTS boletim_conjuntura (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fk_documento_id INTEGER NOT NULL,       -- Linhagem: referência ao catálogo
                ano INTEGER NOT NULL,
                trimestre INTEGER NOT NULL,
                empresa TEXT NOT NULL,

                -- Lançamentos
                lanc_vs_tri_anterior REAL,
                lanc_vs_mesmo_tri_ano_ant REAL,
                lanc_acum_9m_ano_ant REAL,
                lanc_acum_9m_atual REAL,

                -- Vendas
                vend_vs_tri_anterior REAL,
                vend_vs_mesmo_tri_ano_ant REAL,
                vend_acum_9m_ano_ant REAL,
                vend_acum_9m_atual REAL,

                criado_em TEXT DEFAULT (datetime('now')),

                FOREIGN KEY (fk_documento_id) REFERENCES catalogo_documentos(id),
                UNIQUE(fk_documento_id, empresa)        -- Evita duplicatas por doc+empresa
            );

            -- Totais agregados do setor por trimestre
            CREATE TABLE IF NOT EXISTS totais_setor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fk_documento_id INTEGER NOT NULL,
                ano INTEGER NOT NULL,
                trimestre INTEGER NOT NULL,

                -- Lançamentos totais
                total_lanc_vs_tri_anterior REAL,
                total_lanc_vs_mesmo_tri_ano_ant REAL,
                total_lanc_acum_9m_ano_ant REAL,
                total_lanc_acum_9m_atual REAL,

                -- Vendas totais
                total_vend_vs_tri_anterior REAL,
                total_vend_vs_mesmo_tri_ano_ant REAL,
                total_vend_acum_9m_ano_ant REAL,
                total_vend_acum_9m_atual REAL,

                observacao_editorial TEXT,
                criado_em TEXT DEFAULT (datetime('now')),

                FOREIGN KEY (fk_documento_id) REFERENCES catalogo_documentos(id)
            );
        """)
    print(f"[DB] Banco inicializado em: {os.path.abspath(DB_PATH)}")


@contextmanager
def get_conn():
    """Context manager para conexões com o SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def calcular_hash_arquivo(caminho_arquivo: str) -> str:
    """
    Calcula o hash SHA-256 de um arquivo PDF.
    Usado para garantir idempotência: se o hash já existir no catálogo,
    o arquivo não será reprocessado (evitando custos desnecessários de API).
    """
    sha256 = hashlib.sha256()
    with open(caminho_arquivo, "rb") as f:
        for bloco in iter(lambda: f.read(8192), b""):
            sha256.update(bloco)
    return sha256.hexdigest()


def calcular_hash_url(url: str) -> str:
    """Calcula hash SHA-256 de uma URL (útil quando não temos o arquivo localmente)."""
    return hashlib.sha256(url.encode()).hexdigest()


def documento_ja_processado(hash_sha256: str) -> bool:
    """
    Verifica se um documento já foi processado com sucesso.
    Consulta o catálogo pelo hash SHA-256.
    Retorna True se já existe e foi processado com sucesso.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM catalogo_documentos WHERE hash_sha256 = ?",
            (hash_sha256,)
        ).fetchone()
        if row and row["status"] == "processado":
            print(f"[IDEMPOTÊNCIA] Documento com hash {hash_sha256[:12]}... já processado. Ignorando.")
            return True
        return False


def registrar_documento(
    hash_sha256: str,
    url_origem: str,
    nome_arquivo: str,
    empresa_fonte: str,
    ano_referencia: Optional[int] = None,
    trimestre_referencia: Optional[int] = None
) -> int:
    """
    Registra um novo documento no catálogo.
    Retorna o ID do registro criado.
    """
    with get_conn() as conn:
        # Se já existe com status 'pendente' ou 'erro', atualiza; senão insere
        existing = conn.execute(
            "SELECT id FROM catalogo_documentos WHERE hash_sha256 = ?",
            (hash_sha256,)
        ).fetchone()

        if existing:
            return existing["id"]

        cursor = conn.execute(
            """INSERT INTO catalogo_documentos
               (hash_sha256, url_origem, nome_arquivo, empresa_fonte,
                data_coleta, status, ano_referencia, trimestre_referencia)
               VALUES (?, ?, ?, ?, ?, 'pendente', ?, ?)""",
            (hash_sha256, url_origem, nome_arquivo, empresa_fonte,
             datetime.now().isoformat(), ano_referencia, trimestre_referencia)
        )
        doc_id = cursor.lastrowid
        print(f"[CATÁLOGO] Documento registrado. ID={doc_id}, arquivo={nome_arquivo}")
        return doc_id


def marcar_processado(doc_id: int, ano: int, trimestre: int):
    """Marca um documento como processado com sucesso no catálogo."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE catalogo_documentos
               SET status = 'processado',
                   data_processamento = ?,
                   ano_referencia = ?,
                   trimestre_referencia = ?
               WHERE id = ?""",
            (datetime.now().isoformat(), ano, trimestre, doc_id)
        )
    print(f"[CATÁLOGO] Documento ID={doc_id} marcado como processado.")


def marcar_erro(doc_id: int, mensagem_erro: str):
    """Marca um documento como erro no catálogo."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE catalogo_documentos
               SET status = 'erro', erro_mensagem = ?
               WHERE id = ?""",
            (mensagem_erro, doc_id)
        )
    print(f"[CATÁLOGO] Documento ID={doc_id} marcado como erro: {mensagem_erro}")


def salvar_dados_boletim(doc_id: int, boletim) -> None:
    """
    Persiste os dados extraídos do boletim no banco.
    O parâmetro boletim é um objeto BoletimConjuntura validado pelo Pydantic.
    A linhagem é garantida pelo fk_documento_id.
    """
    with get_conn() as conn:
        # Salvar dados de cada empresa
        for empresa in boletim.empresas:
            conn.execute(
                """INSERT OR REPLACE INTO boletim_conjuntura
                   (fk_documento_id, ano, trimestre, empresa,
                    lanc_vs_tri_anterior, lanc_vs_mesmo_tri_ano_ant,
                    lanc_acum_9m_ano_ant, lanc_acum_9m_atual,
                    vend_vs_tri_anterior, vend_vs_mesmo_tri_ano_ant,
                    vend_acum_9m_ano_ant, vend_acum_9m_atual)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id, boletim.ano, boletim.trimestre, empresa.empresa,
                    empresa.lancamentos_vs_trimestre_anterior,
                    empresa.lancamentos_vs_mesmo_trimestre_ano_anterior,
                    empresa.lancamentos_acumulado_9m_ano_anterior,
                    empresa.lancamentos_acumulado_9m_atual,
                    empresa.vendas_vs_trimestre_anterior,
                    empresa.vendas_vs_mesmo_trimestre_ano_anterior,
                    empresa.vendas_acumulado_9m_ano_anterior,
                    empresa.vendas_acumulado_9m_atual
                )
            )

        # Salvar totais do setor
        if boletim.totais_setor:
            t = boletim.totais_setor
            conn.execute(
                """INSERT OR REPLACE INTO totais_setor
                   (fk_documento_id, ano, trimestre,
                    total_lanc_vs_tri_anterior, total_lanc_vs_mesmo_tri_ano_ant,
                    total_lanc_acum_9m_ano_ant, total_lanc_acum_9m_atual,
                    total_vend_vs_tri_anterior, total_vend_vs_mesmo_tri_ano_ant,
                    total_vend_acum_9m_ano_ant, total_vend_acum_9m_atual,
                    observacao_editorial)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id, boletim.ano, boletim.trimestre,
                    t.total_lancamentos_vs_trimestre_anterior,
                    t.total_lancamentos_vs_mesmo_trimestre_ano_anterior,
                    t.total_lancamentos_acumulado_9m_ano_anterior,
                    t.total_lancamentos_acumulado_9m_atual,
                    t.total_vendas_vs_trimestre_anterior,
                    t.total_vendas_vs_mesmo_trimestre_ano_anterior,
                    t.total_vendas_acumulado_9m_ano_anterior,
                    t.total_vendas_acumulado_9m_atual,
                    boletim.observacao_editorial
                )
            )

    print(f"[DB] Dados do boletim {boletim.ano}/T{boletim.trimestre} salvos com sucesso.")
