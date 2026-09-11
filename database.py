import os
import sqlite3
import requests
import pandas as pd
from datetime import datetime
import bcrypt

# Sessão HTTP reutilizada para todas as chamadas ao Turso. Usar uma única
# requests.Session (em vez de requests.post avulso a cada chamada) permite
# manter a conexão TCP/TLS viva (keep-alive) entre uma consulta e outra,
# evitando pagar um novo handshake a cada requisição — a maior causa de
# lentidão percebida no app, já que cada tela faz várias consultas.
_http_session = requests.Session()

# Cache leve para listas de referência (funcionários, feriados, usuários) que
# mudam raramente mas são consultadas em quase toda tela. Usa st.cache_data
# quando o Streamlit está disponível (produção); vira um "no-op" transparente
# fora dele (ex.: scripts/testes locais que importam este módulo sozinho).
try:
    import streamlit as _st_cache
    _cache_data = _st_cache.cache_data
except Exception:
    def _cache_data(*args, **kwargs):
        def _decorator(func):
            func.clear = lambda: None  # compatível com as chamadas .clear() abaixo
            return func
        return _decorator


def _limpar_cache(func):
    """Invalida o cache de uma função decorada com @_cache_data, se houver."""
    clear = getattr(func, "clear", None)
    if callable(clear):
        clear()

# ============= CONEXÃO COM O BANCO (Turso / libSQL na nuvem) =============
#
# Este app roda no Streamlit Community Cloud, cujo disco local é apagado a
# cada reinício/deploy. Por isso os dados NÃO ficam mais num arquivo .db
# local: ficam no Turso (banco SQLite hospedado na nuvem, gratuito).
#
# A conexão é feita via API HTTP do Turso (protocolo "Hrana v2"), usando
# apenas a biblioteca "requests" (sem depender de pacotes experimentais).
# Toda a lógica de SQL do restante deste arquivo permanece igual à versão
# original em SQLite local (mesmos nomes de função, mesmos placeholders "?").

def _get_config(nome):
    """Lê a configuração primeiro dos Secrets do Streamlit Cloud e, se não
    encontrar (ex.: rodando localmente), cai para variável de ambiente."""
    try:
        import streamlit as st
        if nome in st.secrets:
            return st.secrets[nome]
    except Exception:
        pass
    return os.environ.get(nome)


TURSO_DATABASE_URL = _get_config("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = _get_config("TURSO_AUTH_TOKEN")


def _to_arg(v):
    """Converte um valor Python para o formato de argumento tipado que a
    API do Turso espera."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def _from_val(cell):
    """Converte um valor tipado devolvido pela API do Turso de volta para
    um tipo nativo do Python (igual ao que o sqlite3 devolveria)."""
    t = cell.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(cell["value"])
    if t == "float":
        return float(cell["value"])
    if t == "text":
        return cell["value"]
    if t == "blob":
        return cell.get("value")
    return cell.get("value")


class TursoCursor:
    """Objeto compatível com um cursor do sqlite3 (o suficiente para o que
    este projeto usa: execute, fetchone, fetchall, description, lastrowid)."""

    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self._rows = []
        self.lastrowid = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        cols, rows, lastrowid, affected = self._conn._raw_execute(sql, params)
        self.description = [(c,) for c in cols] if cols else None
        self._rows = rows
        self.lastrowid = lastrowid
        self.rowcount = affected
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class TursoConnection:
    """Conexão compatível com o suficiente da API do sqlite3.Connection
    (execute, cursor, commit, close) para não precisar reescrever o
    restante deste arquivo."""

    def __init__(self, url, token):
        # Aceita tanto "libsql://xxx.turso.io" quanto "https://xxx.turso.io"
        http_url = url.replace("libsql://", "https://").replace("wss://", "https://")
        self.http_url = http_url.rstrip("/") + "/v2/pipeline"
        self.token = token

    def _raw_execute(self, sql, params=None):
        args = [_to_arg(p) for p in (params or [])]
        stmt = {"sql": sql}
        if args:
            stmt["args"] = args
        body = {"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]}
        resp = _http_session.post(
            self.http_url,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        j = resp.json()
        result = j["results"][0]
        if result["type"] == "error":
            msg = result.get("error", {}).get("message", "Erro no banco de dados")
            code = result.get("error", {}).get("code", "")
            if "CONSTRAINT" in code:
                raise sqlite3.IntegrityError(msg)
            raise sqlite3.OperationalError(msg)
        r = result["response"]["result"]
        cols = [c["name"] for c in r.get("cols", [])]
        rows = [[_from_val(cell) for cell in row] for row in r.get("rows", [])]
        lastrowid = r.get("last_insert_rowid")
        lastrowid = int(lastrowid) if lastrowid not in (None, "") else None
        affected = r.get("affected_row_count", 0) or 0
        return cols, rows, lastrowid, affected

    def execute(self, sql, params=None):
        cur = TursoCursor(self)
        cur.execute(sql, params)
        return cur

    def cursor(self):
        return TursoCursor(self)

    def commit(self):
        # Cada chamada HTTP já é commitada individualmente pelo Turso
        # (não há transação pendente para confirmar).
        pass

    def close(self):
        pass

def conectar():
    if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
        raise RuntimeError(
            "Configuração do banco na nuvem não encontrada. Defina "
            "TURSO_DATABASE_URL e TURSO_AUTH_TOKEN nos Secrets do Streamlit "
            "(ou como variáveis de ambiente)."
        )
    return TursoConnection(TURSO_DATABASE_URL, TURSO_AUTH_TOKEN)


def _query_df(sql, params=None):
    """Executa uma consulta e devolve um pandas DataFrame (substitui o uso
    de pd.read_sql, que dependia da API interna do sqlite3)."""
    conn = conectar()
    cur = conn.cursor()
    cur.execute(sql, params or [])
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=cols)


def inicializar_banco():
    """Cria todas as tabelas necessárias no banco de dados."""
    conn = conectar()
    c = conn.cursor()

    # Tabela de Usuários (NOVO)
    c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            eh_admin BOOLEAN DEFAULT 0,
            id_funcionario INTEGER REFERENCES funcionarios(id),
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela de Funcionários
    c.execute("""
        CREATE TABLE IF NOT EXISTS funcionarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL
        )
    """)

    # Tabela de Registros Diários
    c.execute("""
        CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data DATE NOT NULL,
            id_funcionario INTEGER NOT NULL,
            entrada TEXT,
            saida TEXT,
            saldo_decimal REAL NOT NULL,
            descontou_almoco BOOLEAN,
            falta_injustificada BOOLEAN,
            FOREIGN KEY(id_funcionario) REFERENCES funcionarios(id),
            UNIQUE(data, id_funcionario)
        )
    """)

    # Tabela de Ajustes (Extrato)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ajustes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data DATE NOT NULL,
            id_funcionario INTEGER NOT NULL,
            valor_decimal REAL NOT NULL,
            motivo TEXT NOT NULL,
            FOREIGN KEY(id_funcionario) REFERENCES funcionarios(id)
        )
    """)

    # Tabela de Feriados
    c.execute("""
        CREATE TABLE IF NOT EXISTS feriados (
            data DATE PRIMARY KEY,
            descricao TEXT
        )
    """)

    # Migração: garantir que bancos antigos (criados antes desta versão) ganhem
    # a coluna id_funcionario na tabela usuarios, sem perder dados existentes.
    c.execute("PRAGMA table_info(usuarios)")
    colunas_usuarios = [col[1] for col in c.fetchall()]
    if 'id_funcionario' not in colunas_usuarios:
        c.execute("ALTER TABLE usuarios ADD COLUMN id_funcionario INTEGER REFERENCES funcionarios(id)")

    # Verificar se há usuário admin, se não houver criar um padrão de emergência
    # (isso só deve acontecer se todos os administradores forem removidos)
    c.execute("SELECT COUNT(*) FROM usuarios WHERE eh_admin = 1")
    if c.fetchone()[0] == 0:
        try:
            senha_hash = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode('utf-8')
            c.execute("INSERT INTO usuarios (username, senha_hash, eh_admin) VALUES (?, ?, ?)",
                     ("admin", senha_hash, 1))
        except sqlite3.IntegrityError:
            # Usuário 'admin' já existe (dado legado) - ignorar
            pass

    conn.close()

    # Popular feriados padrão (Rio de Janeiro) na primeira execução
    seed_feriados_padrao()

# ============= FUNÇÕES DE AUTENTICAÇÃO =============

def hash_senha(senha):
    """Gera hash seguro da senha usando bcrypt."""
    return bcrypt.hashpw(senha.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verificar_senha(senha, hash_armazenado):
    """Verifica se a senha corresponde ao hash armazenado."""
    return bcrypt.checkpw(senha.encode('utf-8'), hash_armazenado.encode('utf-8'))

def criar_usuario(username, senha, eh_admin=False, id_funcionario=None):
    """Cria um novo usuário. Se id_funcionario for informado, este login fica
    restrito a lançar o ponto apenas desse funcionário (sem editar/excluir)."""
    conn = conectar()
    try:
        senha_hash = hash_senha(senha)
        conn.execute("INSERT INTO usuarios (username, senha_hash, eh_admin, id_funcionario) VALUES (?, ?, ?, ?)",
                    (username, senha_hash, eh_admin, id_funcionario))
        conn.commit()
        _limpar_cache(listar_usuarios)
        return True, "Usuário criado com sucesso!"
    except sqlite3.IntegrityError:
        return False, "Usuário já existe!"
    except Exception as e:
        return False, f"Erro ao criar usuário: {e}"
    finally:
        conn.close()

def autenticar_usuario(username, senha):
    """Autentica um usuário. Retorna (sucesso, username, eh_admin, id_funcionario)."""
    conn = conectar()
    cursor = conn.execute("SELECT id, senha_hash, eh_admin, id_funcionario FROM usuarios WHERE username = ?", (username,))
    resultado = cursor.fetchone()
    conn.close()

    if resultado is None:
        return False, None, None, None

    id_usuario, hash_armazenado, eh_admin, id_funcionario = resultado
    if verificar_senha(senha, hash_armazenado):
        return True, username, eh_admin, id_funcionario
    return False, None, None, None

@_cache_data(ttl=30)
def listar_usuarios():
    """Lista todos os usuários (apenas para admin), com o nome do funcionário vinculado.
    Cacheado por 30s (invalidado explicitamente ao criar/deletar usuário ou
    alterar permissão de admin) para evitar uma ida ao banco a cada rerun."""
    return _query_df("""
        SELECT u.id, u.username, u.eh_admin,
               COALESCE(f.nome, '—') as funcionario_vinculado,
               u.criado_em
        FROM usuarios u
        LEFT JOIN funcionarios f ON f.id = u.id_funcionario
        ORDER BY u.username
    """)

def deletar_usuario(username):
    """Deleta um usuário."""
    conn = conectar()
    try:
        conn.execute("DELETE FROM usuarios WHERE username = ?", (username,))
        conn.commit()
        _limpar_cache(listar_usuarios)
        return True, "Usuário deletado!"
    except Exception as e:
        return False, f"Erro ao deletar: {e}"
    finally:
        conn.close()

def alterar_admin(username, eh_admin):
    """Altera permissão de admin do usuário."""
    conn = conectar()
    try:
        conn.execute("UPDATE usuarios SET eh_admin = ? WHERE username = ?", (eh_admin, username))
        conn.commit()
        _limpar_cache(listar_usuarios)
        return True, "Permissão atualizada!"
    except Exception as e:
        return False, f"Erro: {e}"
    finally:
        conn.close()

def alterar_senha(username, senha_atual, senha_nova):
    """Permite que o próprio usuário troque sua senha, validando a senha atual."""
    conn = conectar()
    try:
        cursor = conn.execute("SELECT senha_hash FROM usuarios WHERE username = ?", (username,))
        resultado = cursor.fetchone()
        if resultado is None:
            return False, "Usuário não encontrado."

        hash_atual = resultado[0]
        if not verificar_senha(senha_atual, hash_atual):
            return False, "Senha atual incorreta."

        novo_hash = hash_senha(senha_nova)
        conn.execute("UPDATE usuarios SET senha_hash = ? WHERE username = ?", (novo_hash, username))
        conn.commit()
        return True, "Senha alterada com sucesso!"
    except Exception as e:
        return False, f"Erro ao alterar senha: {e}"
    finally:
        conn.close()

def redefinir_senha_admin(username_alvo, nova_senha):
    """Permite que um administrador redefina a senha de qualquer usuário,
    sem precisar informar a senha atual."""
    conn = conectar()
    try:
        cursor = conn.execute("SELECT id FROM usuarios WHERE username = ?", (username_alvo,))
        if cursor.fetchone() is None:
            return False, "Usuário não encontrado."

        novo_hash = hash_senha(nova_senha)
        conn.execute("UPDATE usuarios SET senha_hash = ? WHERE username = ?", (novo_hash, username_alvo))
        conn.commit()
        return True, f"Senha de '{username_alvo}' redefinida com sucesso!"
    except Exception as e:
        return False, f"Erro ao redefinir senha: {e}"
    finally:
        conn.close()

# ============= FUNÇÕES DE FUNCIONÁRIOS =============

def adicionar_funcionario(nome):
    """Adiciona um novo funcionário."""
    conn = conectar()
    try:
        conn.execute("INSERT INTO funcionarios (nome) VALUES (?)", (nome,))
        conn.commit()
        _limpar_cache(listar_funcionarios)
        return True
    except sqlite3.IntegrityError:
        return False  # Já existe
    finally:
        conn.close()

@_cache_data(ttl=30)
def listar_funcionarios():
    """Lista todos os funcionários. Cacheado por 30s (invalidado explicitamente
    ao adicionar/remover funcionário) — é a consulta mais repetida do app,
    chamada em praticamente toda tela."""
    return _query_df("SELECT * FROM funcionarios ORDER BY nome")

def deletar_funcionario(id_funcionario):
    """Deleta um funcionário e seus registros."""
    conn = conectar()
    try:
        conn.execute("DELETE FROM registros WHERE id_funcionario = ?", (id_funcionario,))
        conn.execute("DELETE FROM ajustes WHERE id_funcionario = ?", (id_funcionario,))
        conn.execute("DELETE FROM funcionarios WHERE id = ?", (id_funcionario,))
        conn.commit()
        _limpar_cache(listar_funcionarios)
        return True
    except Exception as e:
        return False
    finally:
        conn.close()

# ============= FUNÇÕES DE REGISTROS =============

def registrar_ponto(data, id_funcionario, entrada, saida, saldo_decimal, descontou_almoco, falta_injustificada):
    """Registra um ponto (entrada/saída)."""
    conn = conectar()
    try:
        conn.execute("""
            INSERT INTO registros (data, id_funcionario, entrada, saida, saldo_decimal, descontou_almoco, falta_injustificada)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data, id_funcionario, entrada, saida, saldo_decimal, descontou_almoco, falta_injustificada))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        return False
    finally:
        conn.close()

def registrar_entrada(data, id_funcionario, entrada):
    """Registra apenas a entrada."""
    conn = conectar()
    try:
        conn.execute("""
            INSERT INTO registros (data, id_funcionario, entrada, saida, saldo_decimal, descontou_almoco, falta_injustificada)
            VALUES (?, ?, ?, '', 0.0, 0, 0)
        """, (data, id_funcionario, entrada))
        conn.commit()
        return True
    except Exception as e:
        return False
    finally:
        conn.close()

def registrar_saida(data, id_funcionario, saida, saldo_decimal, descontou_almoco):
    """Registra a saída e calcula o saldo."""
    conn = conectar()
    try:
        conn.execute("""
            UPDATE registros
            SET saida = ?, saldo_decimal = ?, descontou_almoco = ?
            WHERE data = ? AND id_funcionario = ?
        """, (saida, saldo_decimal, descontou_almoco, data, id_funcionario))
        conn.commit()
        return True
    except Exception as e:
        return False
    finally:
        conn.close()

def obter_registro_diario(data, id_funcionario):
    """Obtém o registro de um funcionário em uma data específica."""
    conn = conectar()
    cursor = conn.execute("SELECT entrada, saida, falta_injustificada FROM registros WHERE data = ? AND id_funcionario = ?", (data, id_funcionario))
    row = cursor.fetchone()
    conn.close()
    return row

def atualizar_ponto_completo(data, id_funcionario, entrada, saida, saldo_decimal, descontou_almoco, falta_injustificada):
    """Atualiza um ponto completo (edição/correção)."""
    conn = conectar()
    try:
        conn.execute("""
            UPDATE registros
            SET entrada = ?, saida = ?, saldo_decimal = ?, descontou_almoco = ?, falta_injustificada = ?
            WHERE data = ? AND id_funcionario = ?
        """, (entrada, saida, saldo_decimal, descontou_almoco, falta_injustificada, data, id_funcionario))
        conn.commit()
        return True
    except Exception as e:
        return False
    finally:
        conn.close()

def excluir_ponto(data, id_funcionario):
    """Deleta um registro de ponto."""
    conn = conectar()
    try:
        conn.execute("DELETE FROM registros WHERE data = ? AND id_funcionario = ?", (data, id_funcionario))
        conn.commit()
        return True
    except Exception as e:
        return False
    finally:
        conn.close()

# ============= FUNÇÕES DE AJUSTES =============

def registrar_ajuste(data, id_funcionario, valor_decimal, motivo):
    """Registra um ajuste (saque/crédito)."""
    conn = conectar()
    try:
        conn.execute("INSERT INTO ajustes (data, id_funcionario, valor_decimal, motivo) VALUES (?, ?, ?, ?)",
                    (data, id_funcionario, valor_decimal, motivo))
        conn.commit()
        return True
    except Exception as e:
        return False
    finally:
        conn.close()

# ============= FUNÇÕES DE EXTRATO =============

def obter_extrato_funcionario(id_funcionario, data_inicio=None, data_fim=None):
    """Obtém o extrato de um funcionário, opcionalmente filtrado por data."""
    if data_inicio and data_fim:
        query = """
            SELECT data as Data, saldo_decimal as Movimentacao, 'Registro Diário' as Tipo, entrada || ' às ' || saida as Detalhe
            FROM registros
            WHERE id_funcionario = ? AND data BETWEEN ? AND ?
            UNION ALL
            SELECT data as Data, valor_decimal as Movimentacao, 'Ajuste/Saque' as Tipo, motivo as Detalhe
            FROM ajustes
            WHERE id_funcionario = ? AND data BETWEEN ? AND ?
            ORDER BY Data ASC
        """
        return _query_df(query, (id_funcionario, data_inicio, data_fim, id_funcionario, data_inicio, data_fim))
    else:
        query = """
            SELECT data as Data, saldo_decimal as Movimentacao, 'Registro Diário' as Tipo, entrada || ' às ' || saida as Detalhe
            FROM registros
            WHERE id_funcionario = ?
            UNION ALL
            SELECT data as Data, valor_decimal as Movimentacao, 'Ajuste/Saque' as Tipo, motivo as Detalhe
            FROM ajustes
            WHERE id_funcionario = ?
            ORDER BY Data ASC
        """
        return _query_df(query, (id_funcionario, id_funcionario))

def listar_meses_disponiveis():
    """Retorna a lista de competências (ano, mês) com algum registro ou ajuste
    lançado, da mais recente para a mais antiga, sempre incluindo o mês atual."""
    query = """
        SELECT DISTINCT strftime('%Y-%m', data) as competencia FROM registros
        UNION
        SELECT DISTINCT strftime('%Y-%m', data) as competencia FROM ajustes
    """
    df = _query_df(query)

    competencias = set(df['competencia'].dropna().tolist())
    competencias.add(datetime.now().strftime('%Y-%m'))

    meses = []
    for comp in competencias:
        ano, mes = comp.split('-')
        meses.append((int(ano), int(mes)))

    meses.sort(reverse=True)
    return meses

def obter_saldo_atual(id_funcionario):
    """Obtém o saldo atual (acumulado) de um funcionário."""
    conn = conectar()

    query = """
        SELECT COALESCE(SUM(movimentacao), 0.0) as saldo
        FROM (
            SELECT saldo_decimal as movimentacao FROM registros WHERE id_funcionario = ?
            UNION ALL
            SELECT valor_decimal as movimentacao FROM ajustes WHERE id_funcionario = ?
        )
    """

    cursor = conn.execute(query, (id_funcionario, id_funcionario))
    saldo = cursor.fetchone()[0]
    conn.close()
    return round(saldo, 2)

# ============= FUNÇÕES DE FERIADOS =============

def verificar_feriado(data_str):
    """Verifica se uma data é feriado."""
    conn = conectar()
    cursor = conn.execute("SELECT * FROM feriados WHERE data = ?", (data_str,))
    feriado = cursor.fetchone()
    conn.close()
    return feriado is not None

@_cache_data(ttl=30)
def listar_feriados():
    """Lista todos os feriados cadastrados. Cacheado por 30s (invalidado
    explicitamente ao adicionar/remover feriado)."""
    return _query_df("SELECT * FROM feriados ORDER BY data")

def adicionar_feriado(data, descricao):
    """Adiciona um novo feriado."""
    conn = conectar()
    try:
        conn.execute("INSERT INTO feriados (data, descricao) VALUES (?, ?)", (data, descricao))
        conn.commit()
        _limpar_cache(listar_feriados)
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remover_feriado(data):
    """Remove um feriado."""
    conn = conectar()
    try:
        conn.execute("DELETE FROM feriados WHERE data = ?", (data,))
        conn.commit()
        _limpar_cache(listar_feriados)
        return True
    except Exception as e:
        return False
    finally:
        conn.close()

# ============= BACKUP COMPLETO (ADMIN) =============

def obter_backup_completo():
    """Retorna um dicionário {nome_tabela: DataFrame} com o conteúdo bruto e
    completo de todas as tabelas do banco (Turso), para uso em backups feitos
    pelo administrador. Não é cacheado (deve sempre refletir o estado real no
    momento em que o backup é gerado)."""
    return {
        "usuarios": _query_df("SELECT * FROM usuarios ORDER BY id"),
        "funcionarios": _query_df("SELECT * FROM funcionarios ORDER BY id"),
        "registros": _query_df("SELECT * FROM registros ORDER BY data, id_funcionario"),
        "ajustes": _query_df("SELECT * FROM ajustes ORDER BY data, id_funcionario"),
        "feriados": _query_df("SELECT * FROM feriados ORDER BY data"),
    }

# Feriados de 2026 aplicáveis à cidade do Rio de Janeiro (nacionais + estaduais
# RJ + municipais Rio de Janeiro). Pesquisado em 10/09/2026. Esta lista é apenas
# uma pré-configuração inicial: pode ser livremente editada, complementada
# (ex.: anos seguintes) ou corrigida na aba Configurações > Feriados.
FERIADOS_RJ_2026 = [
    ("2026-01-01", "Confraternização Universal (Nacional)"),
    ("2026-01-20", "Dia de São Sebastião (Municipal - Rio de Janeiro)"),
    ("2026-02-16", "Carnaval - Segunda-feira (Ponto Facultativo)"),
    ("2026-02-17", "Carnaval (Nacional)"),
    ("2026-04-03", "Sexta-feira Santa (Nacional)"),
    ("2026-04-21", "Tiradentes (Nacional)"),
    ("2026-04-23", "Dia de São Jorge (Estadual - RJ / Municipal - Rio de Janeiro)"),
    ("2026-05-01", "Dia do Trabalho (Nacional)"),
    ("2026-06-04", "Corpus Christi (Ponto Facultativo Nacional)"),
    ("2026-09-07", "Independência do Brasil (Nacional)"),
    ("2026-10-12", "Nossa Senhora Aparecida (Nacional)"),
    ("2026-11-02", "Finados (Nacional)"),
    ("2026-11-15", "Proclamação da República (Nacional)"),
    ("2026-11-20", "Dia Nacional de Zumbi e da Consciência Negra (Nacional)"),
    ("2026-12-25", "Natal (Nacional)"),
]

def seed_feriados_padrao():
    """Pré-popula a tabela de feriados com o calendário do Rio de Janeiro para
    2026, apenas se ainda não houver nenhum feriado cadastrado. Não sobrescreve
    nem duplica feriados já existentes/editados pelo usuário."""
    conn = conectar()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM feriados")
    ja_tem_feriados = c.fetchone()[0] > 0
    conn.close()

    if ja_tem_feriados:
        return

    for data_feriado, descricao in FERIADOS_RJ_2026:
        adicionar_feriado(data_feriado, descricao)
