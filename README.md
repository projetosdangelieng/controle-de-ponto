# Controle de Ponto — D'Angeli Engenharia

App em Streamlit para lançamento de horário dos funcionários (entrada, saída,
faltas, atestados, férias), relatórios e exportação.

## Arquitetura (versão hospedada na nuvem)

- **Código**: este repositório (GitHub), implantado no **Streamlit Community Cloud**.
- **Banco de dados**: [Turso](https://turso.tech) — banco SQLite hospedado na
  nuvem (compatível com o mesmo SQL da versão local), acessado via API HTTP
  em `database.py`. Isso resolve o problema do Streamlit Cloud apagar
  arquivos locais a cada reinício: os dados agora persistem na nuvem, não no
  disco do servidor.

## Configuração dos Secrets (Streamlit Cloud)

Em **share.streamlit.io → seu app → Settings → Secrets**, adicione:

```toml
TURSO_DATABASE_URL = "libsql://controle-de-ponto-dangeli.aws-us-east-1.turso.io"
TURSO_AUTH_TOKEN = "..."
```

Veja `.streamlit/secrets.toml.example` para o modelo (nunca commitar o
arquivo `secrets.toml` real).

## Rodando localmente

```bash
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
# edite .streamlit/secrets.toml com o token real
streamlit run app.py
```

## Usuários

- **Fellipe** — administrador (acesso completo).
- **Marcelo**, **Nathalya**, **Julia** — usuários restritos, cada um só
  lança o próprio ponto (entrada), sem editar/excluir.
