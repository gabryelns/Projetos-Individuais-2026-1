"""
Contrato Semântico dos Dados (Pydantic Schema)
================================================
Define as regras de negócio e validação dos dados extraídos pelo LLM.
Força tipos corretos e trata valores ausentes como None (NULL no banco).
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class VariacaoEmpresa(BaseModel):
    """
    Representa as variações percentuais de uma empresa em um trimestre.
    Todos os valores são OPCIONAIS (None = NULL) para não alucinação.
    """
    empresa: str = Field(..., description="Nome da empresa construtora")

    # Variações de Lançamentos
    lancamentos_vs_trimestre_anterior: Optional[float] = Field(
        None,
        description="Variação percentual dos lançamentos vs trimestre anterior (ex: -32 para -32%)"
    )
    lancamentos_vs_mesmo_trimestre_ano_anterior: Optional[float] = Field(
        None,
        description="Variação percentual dos lançamentos vs mesmo trimestre do ano anterior"
    )
    lancamentos_acumulado_9m_ano_anterior: Optional[float] = Field(
        None,
        description="Variação percentual acumulada 9 meses vs 9 meses do ano anterior"
    )
    lancamentos_acumulado_9m_atual: Optional[float] = Field(
        None,
        description="Variação percentual acumulada 9 meses do ano atual vs 9 meses do ano anterior"
    )

    # Variações de Vendas
    vendas_vs_trimestre_anterior: Optional[float] = Field(
        None,
        description="Variação percentual das vendas vs trimestre anterior"
    )
    vendas_vs_mesmo_trimestre_ano_anterior: Optional[float] = Field(
        None,
        description="Variação percentual das vendas vs mesmo trimestre do ano anterior"
    )
    vendas_acumulado_9m_ano_anterior: Optional[float] = Field(
        None,
        description="Variação percentual acumulada vendas 9 meses vs 9 meses do ano anterior"
    )
    vendas_acumulado_9m_atual: Optional[float] = Field(
        None,
        description="Variação percentual acumulada vendas 9 meses do ano atual vs 9 meses do ano anterior"
    )

    @field_validator(
        'lancamentos_vs_trimestre_anterior',
        'lancamentos_vs_mesmo_trimestre_ano_anterior',
        'lancamentos_acumulado_9m_ano_anterior',
        'lancamentos_acumulado_9m_atual',
        'vendas_vs_trimestre_anterior',
        'vendas_vs_mesmo_trimestre_ano_anterior',
        'vendas_acumulado_9m_ano_anterior',
        'vendas_acumulado_9m_atual',
        mode='before'
    )
    @classmethod
    def parse_percentual(cls, v):
        """
        Normaliza valores percentuais:
        - Remove '%', '+', espaços
        - Converte string para float
        - Retorna None se não for possível converter
        """
        if v is None or v == "" or v == "N/A" or v == "-":
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            cleaned = v.replace('%', '').replace('+', '').replace(' ', '').replace(',', '.')
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None


class TotaisSetor(BaseModel):
    """Totais agregados do setor habitacional no trimestre."""
    total_lancamentos_vs_trimestre_anterior: Optional[float] = Field(
        None, description="Variação total de lançamentos vs trimestre anterior"
    )
    total_lancamentos_vs_mesmo_trimestre_ano_anterior: Optional[float] = Field(
        None, description="Variação total de lançamentos vs mesmo trimestre ano anterior"
    )
    total_lancamentos_acumulado_9m_ano_anterior: Optional[float] = Field(
        None, description="Variação acumulada total 9m ano anterior"
    )
    total_lancamentos_acumulado_9m_atual: Optional[float] = Field(
        None, description="Variação acumulada total 9m atual"
    )
    total_vendas_vs_trimestre_anterior: Optional[float] = Field(
        None, description="Variação total de vendas vs trimestre anterior"
    )
    total_vendas_vs_mesmo_trimestre_ano_anterior: Optional[float] = Field(
        None, description="Variação total de vendas vs mesmo trimestre ano anterior"
    )
    total_vendas_acumulado_9m_ano_anterior: Optional[float] = Field(
        None, description="Variação acumulada total vendas 9m ano anterior"
    )
    total_vendas_acumulado_9m_atual: Optional[float] = Field(
        None, description="Variação acumulada total vendas 9m atual"
    )

    @field_validator(
        'total_lancamentos_vs_trimestre_anterior',
        'total_lancamentos_vs_mesmo_trimestre_ano_anterior',
        'total_lancamentos_acumulado_9m_ano_anterior',
        'total_lancamentos_acumulado_9m_atual',
        'total_vendas_vs_trimestre_anterior',
        'total_vendas_vs_mesmo_trimestre_ano_anterior',
        'total_vendas_acumulado_9m_ano_anterior',
        'total_vendas_acumulado_9m_atual',
        mode='before'
    )
    @classmethod
    def parse_percentual(cls, v):
        if v is None or v == "" or v == "N/A" or v == "-":
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            cleaned = v.replace('%', '').replace('+', '').replace(' ', '').replace(',', '.')
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None


class BoletimConjuntura(BaseModel):
    """
    Contrato semântico completo de um Boletim de Conjuntura do Setor Habitacional.
    Este é o schema principal validado pelo LLM antes de persistir no banco.
    """
    ano: int = Field(..., description="Ano de referência do boletim (ex: 2025)")
    trimestre: int = Field(..., ge=1, le=4, description="Trimestre de referência (1, 2, 3 ou 4)")
    empresas: list[VariacaoEmpresa] = Field(
        default_factory=list,
        description="Lista com dados de cada empresa construtora"
    )
    totais_setor: Optional[TotaisSetor] = Field(
        None,
        description="Totais agregados do setor no período"
    )
    observacao_editorial: Optional[str] = Field(
        None,
        description="Texto de destaque ou conclusão editorial presente no boletim"
    )

    @field_validator('trimestre')
    @classmethod
    def trimestre_valido(cls, v):
        if v not in [1, 2, 3, 4]:
            raise ValueError("Trimestre deve ser 1, 2, 3 ou 4")
        return v


class RegistroCatalogo(BaseModel):
    """
    Registro do Catálogo de Dados com linhagem completa.
    Associa cada extração ao documento de origem.
    """
    hash_sha256: str = Field(..., description="Hash SHA-256 do arquivo PDF")
    url_origem: str = Field(..., description="URL de origem do PDF")
    nome_arquivo: str = Field(..., description="Nome do arquivo PDF")
    empresa_fonte: str = Field(..., description="Empresa/fonte que publicou o documento")
    data_coleta: datetime = Field(..., description="Data/hora da coleta do PDF")
    data_processamento: Optional[datetime] = Field(None, description="Data/hora do processamento pelo LLM")
    status: str = Field(
        default="pendente",
        description="Status do processamento: pendente | processado | erro"
    )
    erro_mensagem: Optional[str] = Field(None, description="Mensagem de erro se houver falha")
    ano_referencia: Optional[int] = Field(None, description="Ano de referência dos dados")
    trimestre_referencia: Optional[int] = Field(None, description="Trimestre de referência dos dados")
