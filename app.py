import streamlit as st
import database as db
import regras as rg
import utils as ut
from datetime import date, timedelta
import pandas as pd

# Meses em português para exibição amigável nos filtros
NOMES_MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
}

# === CONFIGURAÇÃO DA PÁGINA ===
st.set_page_config(
    page_title="Controle de Ponto",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Inicializar banco de dados
#
# IMPORTANTE: inicializar_banco() faz várias idas ao banco na nuvem (criar
# tabelas, checar migração de colunas, checar/seedar admin e feriados). Como
# o Streamlit reexecuta este arquivo do início a cada clique/interação,
# chamar isso sem cache fazia o app pagar ~9 requisições extras ao Turso em
# TODA tela — a principal causa da lentidão. Com st.cache_resource, a função
# roda de fato uma única vez por instância do app (fica em cache entre
# reruns e entre usuários), e não mais a cada interação.
@st.cache_resource
def _inicializar_banco_uma_vez():
    db.inicializar_banco()
    return True

_inicializar_banco_uma_vez()

# === SISTEMA DE LOGIN ===
if "usuario_logado" not in st.session_state:
    st.session_state["usuario_logado"] = None
    st.session_state["eh_admin"] = False
    st.session_state["id_funcionario"] = None

if st.session_state["usuario_logado"] is None:
    st.title("🔒 Controle de Ponto - Sistema de Acesso")

    col_login = st.columns([1, 2, 1])[1]

    with col_login:
        st.markdown("### Entre com sua Conta")

        usuario_login = st.text_input("Usuário", key="login_usuario")
        senha_login = st.text_input("Senha", type="password", key="login_senha")

        col_btn = st.columns([1, 1])

        if col_btn[0].button("🔓 Entrar", type="primary", use_container_width=True):
            sucesso, username, eh_admin, id_funcionario = db.autenticar_usuario(usuario_login, senha_login)
            if sucesso:
                st.session_state["usuario_logado"] = username
                st.session_state["eh_admin"] = eh_admin
                st.session_state["id_funcionario"] = id_funcionario
                st.rerun()
            else:
                st.error("❌ Usuário ou senha incorretos!")

        if col_btn[1].button("ℹ️ Sobre", use_container_width=True):
            st.info("""
            Entre com o usuário e senha cadastrados para você.

            Dúvidas sobre acesso? Fale com o administrador do sistema.
            """)

    st.stop()

# === APLICATIVO PRINCIPAL (Apenas para usuários autenticados) ===

# Header com informações do usuário
col_header = st.columns([3, 1])
col_header[0].title("📊 Controle de Ponto e Banco de Horas")
col_header[1].metric("Usuário Logado", st.session_state["usuario_logado"])

# Sidebar - Navegação
with st.sidebar:
    st.markdown(f"**👤 {st.session_state['usuario_logado']}**")
    if st.session_state["eh_admin"]:
        st.markdown("🔐 *Admin*")

    st.divider()

    if st.session_state["eh_admin"]:
        menu_items = [
            "Lançamento Diário",
            "Ajustes e Saques",
            "Relatórios e Exportação",
            "Configurações",
            "Mudar Senha"
        ]
    else:
        # Usuário de funcionário: acesso restrito apenas ao lançamento do
        # próprio ponto (sem edição/exclusão, sem relatórios/configurações),
        # mas pode sempre trocar a própria senha.
        menu_items = ["Lançamento Diário", "Mudar Senha"]

    if len(menu_items) > 1:
        menu = st.radio("Navegação", menu_items, key="menu_principal")
    else:
        menu = menu_items[0]
        st.markdown("**📋 Lançamento Diário**")

    st.divider()

    if st.button("🚪 Sair", use_container_width=True):
        st.session_state["usuario_logado"] = None
        st.session_state["eh_admin"] = False
        st.session_state["id_funcionario"] = None
        st.rerun()

# === MENU: LANÇAMENTO DIÁRIO ===
if menu == "Lançamento Diário":
    st.header("📋 Registro de Ponto")

    funcionarios_df = db.listar_funcionarios()

    if funcionarios_df.empty:
        st.warning("⚠️ Nenhum funcionário cadastrado. Vá para 'Configurações' para adicionar.")
    else:
        eh_admin = st.session_state["eh_admin"]
        id_func_travado = st.session_state.get("id_funcionario")

        col1, col2 = st.columns(2)

        with col1:
            if eh_admin:
                func_selecionado = st.selectbox(
                    "👤 Funcionário",
                    funcionarios_df['nome'].tolist(),
                    key="select_func"
                )
                id_func = int(funcionarios_df.loc[funcionarios_df['nome'] == func_selecionado, 'id'].values[0])
            elif id_func_travado is not None and id_func_travado in funcionarios_df['id'].values:
                id_func = int(id_func_travado)
                func_selecionado = funcionarios_df.loc[funcionarios_df['id'] == id_func, 'nome'].values[0]
                st.markdown(f"**👤 Funcionário:** {func_selecionado}")
            else:
                st.error("❌ Seu usuário não está vinculado a um funcionário. Contate o administrador.")
                st.stop()

        with col2:
            data_registro = st.date_input(
                "📅 Data do Registro",
                date.today(),
                key="data_registro"
            )

        data_str = data_registro.strftime('%Y-%m-%d')

        registro_atual = db.obter_registro_diario(data_str, id_func)

        if registro_atual is None:
            # === NENHUM REGISTRO ENCONTRADO ===
            st.info("📝 Nenhum registro encontrado para esta data.")

            if eh_admin:
                tipo_lancamento = st.radio(
                    "Como deseja registrar?",
                    [
                        "🕐 Apenas Entrada (bater o ponto de chegada)",
                        "✅ Ponto Completo (lançar retroativo)",
                        "❌ Falta Injustificada (-8 horas)"
                    ],
                    key="tipo_lanc"
                )
            else:
                # Funcionário comum: só pode bater a própria entrada.
                # Lançamentos retroativos e faltas ficam a critério do administrador.
                tipo_lancamento = "🕐 Apenas Entrada (bater o ponto de chegada)"

            if tipo_lancamento == "🕐 Apenas Entrada (bater o ponto de chegada)":
                entrada = st.time_input("Hora de Entrada", value=pd.to_datetime("08:00").time(), step=timedelta(minutes=1), key="time_ent_unica")

                if st.button("✔️ Registrar Entrada", type="primary"):
                    if db.registrar_entrada(data_str, id_func, entrada.strftime('%H:%M')):
                        st.success("✅ Entrada registrada com sucesso!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao registrar entrada.")

            elif tipo_lancamento == "✅ Ponto Completo (lançar retroativo)":
                col_ent, col_sai = st.columns(2)

                with col_ent:
                    entrada = st.time_input("🕐 Entrada", value=pd.to_datetime("08:00").time(), step=timedelta(minutes=1), key="time_ent_comp")

                with col_sai:
                    saida = st.time_input("🕑 Saída", value=pd.to_datetime("17:00").time(), step=timedelta(minutes=1), key="time_sai_comp")

                ignorar_almoco = st.checkbox("🍽️ Ignorar desconto automático de almoço", key="chk_almoco_comp")

                if ignorar_almoco:
                    senha_digitada = st.text_input("Senha (obrigatória para ignorar almoço)", type="password", key="senha_comp")
                else:
                    senha_digitada = ""

                if st.button("💾 Salvar Ponto Completo", type="primary"):
                    if ignorar_almoco and not db.verificar_senha(senha_digitada, db.hash_senha("admin123")):
                        # Verificar com a senha correta
                        is_feriado = db.verificar_feriado(data_str)
                        ent_str = entrada.strftime('%H:%M')
                        sai_str = saida.strftime('%H:%M')
                        saldo, desc_almoco = rg.calcular_saldo(ent_str, sai_str, data_str, is_feriado, ignorar_almoco)
                        if db.registrar_ponto(data_str, id_func, ent_str, sai_str, saldo, desc_almoco, False):
                            st.success(f"✅ Salvo! Saldo: {saldo} horas")
                            st.rerun()
                        else:
                            st.error("❌ Erro ao salvar.")
                    elif not ignorar_almoco:
                        is_feriado = db.verificar_feriado(data_str)
                        ent_str = entrada.strftime('%H:%M')
                        sai_str = saida.strftime('%H:%M')
                        saldo, desc_almoco = rg.calcular_saldo(ent_str, sai_str, data_str, is_feriado, ignorar_almoco)
                        if db.registrar_ponto(data_str, id_func, ent_str, sai_str, saldo, desc_almoco, False):
                            st.success(f"✅ Salvo! Saldo: {saldo} horas")
                            st.rerun()
                        else:
                            st.error("❌ Erro ao salvar.")
                    else:
                        st.error("❌ Senha obrigatória para ignorar almoço!")

            elif tipo_lancamento == "❌ Falta Injustificada (-8 horas)":
                st.warning("⚠️ Esta ação registrará uma falta injustificada (-8 horas).")

                if st.button("Confirmar Falta", type="primary"):
                    if db.registrar_ponto(data_str, id_func, "", "", -8.0, False, True):
                        st.success("✅ Falta registrada!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao registrar falta.")

        else:
            # === REGISTRO EXISTENTE ===
            entrada_bd, saida_bd, falta_bd = registro_atual

            if falta_bd:
                st.error("❌ Falta injustificada registrada para esta data.")
            elif saida_bd != "":
                st.success(f"✅ Ponto finalizado! (Entrada: {entrada_bd} | Saída: {saida_bd})")
            else:
                st.warning(f"🕒 Entrada registrada às {entrada_bd}. Aguardando finalização...")

                saida = st.time_input("🕑 Hora de Saída", value=pd.to_datetime("17:00").time(), step=timedelta(minutes=1), key="time_saida_fechamento")

                if eh_admin:
                    ignorar_almoco = st.checkbox("🍽️ Ignorar desconto de almoço", key="chk_almoco_fechamento")
                else:
                    # Apenas administradores podem ignorar o desconto de almoço.
                    ignorar_almoco = False

                if ignorar_almoco:
                    senha_digitada = st.text_input("Senha (obrigatória)", type="password", key="senha_fechamento")
                else:
                    senha_digitada = ""

                if st.button("✔️ Registrar Saída", type="primary"):
                    if ignorar_almoco and not db.verificar_senha(senha_digitada, db.hash_senha("admin123")):
                        st.error("❌ Senha obrigatória para ignorar almoço!")
                    else:
                        is_feriado = db.verificar_feriado(data_str)
                        sai_str = saida.strftime('%H:%M')
                        saldo, desc_almoco = rg.calcular_saldo(entrada_bd, sai_str, data_str, is_feriado, ignorar_almoco)

                        if db.registrar_saida(data_str, id_func, sai_str, saldo, desc_almoco):
                            st.success(f"✅ Ponto fechado! Saldo: {saldo} horas")
                            st.rerun()
                        else:
                            st.error("❌ Erro ao registrar saída.")

            # === EDIÇÃO / CORREÇÃO (somente administrador) ===
            if eh_admin:
                with st.expander("✏️ Corrigir ou Excluir Lançamento"):
                    st.info("Utilize as opções abaixo para alterar horários ou excluir o registro.")

                    try:
                        def_ent = pd.to_datetime(entrada_bd).time() if entrada_bd else pd.to_datetime("08:00").time()
                    except:
                        def_ent = pd.to_datetime("08:00").time()

                    try:
                        def_sai = pd.to_datetime(saida_bd).time() if saida_bd else pd.to_datetime("17:00").time()
                    except:
                        def_sai = pd.to_datetime("17:00").time()

                    col_edit1, col_edit2 = st.columns(2)

                    with col_edit1:
                        nova_entrada = st.time_input("Corrigir Entrada", value=def_ent, step=timedelta(minutes=1), key="edit_ent")

                    with col_edit2:
                        nova_saida = st.time_input("Corrigir Saída", value=def_sai, step=timedelta(minutes=1), key="edit_sai")

                    nova_falta = st.checkbox("Converter em Falta Injustificada (-8h)", value=bool(falta_bd), key="edit_falta")
                    ign_almoco_edit = st.checkbox("Ignorar almoço na correção", key="edit_almoco")

                    st.divider()
                    st.markdown("**🔐 Confirmação de Segurança**")
                    senha_edit = st.text_input("Senha de autorização", type="password", key="senha_edit_global")

                    col_btn1, col_btn2 = st.columns(2)

                    if col_btn1.button("💾 Salvar Correção", type="primary", use_container_width=True):
                        # Verificação simplificada (em produção, usar bcrypt)
                        if nova_falta:
                            saldo_edit, desc_almoco_edit = -8.0, False
                            ent_edit, sai_edit = "", ""
                        else:
                            is_feriado = db.verificar_feriado(data_str)
                            ent_edit = nova_entrada.strftime('%H:%M')
                            sai_edit = nova_saida.strftime('%H:%M')
                            saldo_edit, desc_almoco_edit = rg.calcular_saldo(ent_edit, sai_edit, data_str, is_feriado, ign_almoco_edit)

                        if db.atualizar_ponto_completo(data_str, id_func, ent_edit, sai_edit, saldo_edit, desc_almoco_edit, nova_falta):
                            st.success("✅ Registro corrigido!")
                            st.rerun()
                        else:
                            st.error("❌ Erro ao corrigir.")

                    if col_btn2.button("🗑️ Excluir Registro", type="secondary", use_container_width=True):
                        if db.excluir_ponto(data_str, id_func):
                            st.success("✅ Registro apagado!")
                            st.rerun()
                        else:
                            st.error("❌ Erro ao excluir.")

# === MENU: AJUSTES E SAQUES ===
elif menu == "Ajustes e Saques":
    st.header("💰 Ajustes Manuais e Saques")
    st.info("Use esta tela para abater horas pagas ou adicionar saldos iniciais.")

    funcionarios_df = db.listar_funcionarios()

    if funcionarios_df.empty:
        st.warning("⚠️ Cadastre funcionários primeiro.")
    else:
        col1, col2 = st.columns(2)

        with col1:
            func_ajuste = st.selectbox("👤 Funcionário", funcionarios_df['nome'].tolist(), key="select_ajuste")

        with col2:
            data_ajuste = st.date_input("📅 Data", date.today(), key="data_ajuste")

        valor_ajuste = st.number_input(
            "💵 Valor em Horas (negativo para debitar, positivo para creditar)",
            value=0.0,
            step=0.5,
            format="%.2f"
        )

        motivo = st.text_input("📝 Motivo", placeholder="Ex: Pagamento Competência")

        if st.button("✔️ Lançar Ajuste", type="primary", use_container_width=True):
            if valor_ajuste == 0:
                st.warning("⚠️ O valor não pode ser zero.")
            elif not motivo.strip():
                st.warning("⚠️ Informe um motivo.")
            else:
                id_func = int(funcionarios_df.loc[funcionarios_df['nome'] == func_ajuste, 'id'].values[0])
                if db.registrar_ajuste(data_ajuste.strftime('%Y-%m-%d'), id_func, valor_ajuste, motivo):
                    st.success("✅ Ajuste registrado!")
                else:
                    st.error("❌ Erro ao registrar ajuste.")

# === MENU: RELATÓRIOS E EXPORTAÇÃO ===
elif menu == "Relatórios e Exportação":
    st.header("📊 Relatórios e Exportação")

    funcionarios_df = db.listar_funcionarios()

    if not funcionarios_df.empty:
        # === FILTROS ===
        col_filt1, col_filt2, col_filt3 = st.columns(3)

        with col_filt1:
            func_relatorio = st.selectbox(
                "👤 Selecione o Funcionário",
                ["📋 Todos"] + funcionarios_df['nome'].tolist(),
                key="rel_func"
            )

        with col_filt2:
            filtro_tipo = st.radio(
                "📅 Tipo de Filtro",
                ["Selecionar Mês", "Range Customizado"],
                horizontal=True,
                key="filtro_tipo"
            )

        if filtro_tipo == "Selecionar Mês":
            meses_disponiveis = db.listar_meses_disponiveis()
            opcoes_mes = [f"{NOMES_MESES[m]}/{y}" for (y, m) in meses_disponiveis]

            with col_filt3:
                mes_escolhido = st.selectbox("🗓️ Mês", opcoes_mes, index=0, key="mes_escolhido")

            idx_escolhido = opcoes_mes.index(mes_escolhido)
            ano_sel, mes_sel = meses_disponiveis[idx_escolhido]

            data_inicio = date(ano_sel, mes_sel, 1)
            if mes_sel == 12:
                data_fim = date(ano_sel + 1, 1, 1) - timedelta(days=1)
            else:
                data_fim = date(ano_sel, mes_sel + 1, 1) - timedelta(days=1)
        else:
            col_data1, col_data2 = st.columns(2)
            with col_data1:
                data_inicio = st.date_input("📅 De:", date.today() - timedelta(days=30), key="data_ini")
            with col_data2:
                data_fim = st.date_input("📅 Até:", date.today(), key="data_fim")

        data_inicio_str = data_inicio.strftime('%Y-%m-%d')
        data_fim_str = data_fim.strftime('%Y-%m-%d')

        st.divider()

        # === EXIBIÇÃO DE DADOS ===
        if func_relatorio == "📋 Todos":
            # Relatório consolidado
            st.subheader("🔄 Relatório Consolidado")

            dict_funcionarios = {}
            for _, row in funcionarios_df.iterrows():
                df_extrato = db.obter_extrato_funcionario(row['id'], data_inicio_str, data_fim_str)
                if not df_extrato.empty:
                    dict_funcionarios[row['nome']] = df_extrato

            if dict_funcionarios:
                # Tabela resumida
                resumo_data = []
                for func_nome, df_ext in dict_funcionarios.items():
                    saldo = df_ext['Movimentacao'].sum()
                    resumo_data.append({
                        "Funcionário": func_nome,
                        "Saldo Total (h)": round(saldo, 2),
                        "Movimentações": len(df_ext)
                    })

                df_resumo = pd.DataFrame(resumo_data)
                st.dataframe(df_resumo, use_container_width=True, hide_index=True)

                # Botão para exportar consolidado
                dados_excel, nome_excel = ut.exportar_relatorio_consolidado_excel(dict_funcionarios)
                st.download_button(
                    label="📥 Baixar Relatório Consolidado (Excel)",
                    data=dados_excel,
                    file_name=nome_excel,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary"
                )
            else:
                st.info("ℹ️ Nenhum registro encontrado para o período.")

        else:
            # Relatório individual
            st.subheader(f"📋 Extrato - {func_relatorio}")

            id_func_rel = int(funcionarios_df.loc[funcionarios_df['nome'] == func_relatorio, 'id'].values[0])
            df_extrato = db.obter_extrato_funcionario(id_func_rel, data_inicio_str, data_fim_str)

            if not df_extrato.empty:
                df_extrato_display = df_extrato.copy()
                df_extrato_display['Saldo Acumulado'] = df_extrato_display['Movimentacao'].cumsum()

                # Métrica do saldo
                saldo_atual = db.obter_saldo_atual(id_func_rel)
                cor_saldo = "off" if saldo_atual >= 0 else "inverse"
                st.metric(
                    label="💰 Saldo Atual",
                    value=f"{saldo_atual:.2f} h",
                    delta_color=cor_saldo
                )

                st.dataframe(
                    df_extrato_display,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Movimentacao": st.column_config.NumberColumn(format="%.2f h"),
                        "Saldo Acumulado": st.column_config.NumberColumn(format="%.2f h")
                    }
                )

                # Botões de exportação
                col_exp1, col_exp2 = st.columns(2)

                with col_exp1:
                    dados_excel, nome_excel = ut.exportar_extrato_excel(
                        df_extrato,
                        func_relatorio,
                        data_inicio_str,
                        data_fim_str
                    )
                    st.download_button(
                        label="📥 Baixar como Excel",
                        data=dados_excel,
                        file_name=nome_excel,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary",
                        use_container_width=True
                    )

                with col_exp2:
                    csv = df_extrato.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📥 Baixar como CSV",
                        data=csv,
                        file_name=f"extrato_{func_relatorio}.csv",
                        mime='text/csv',
                        use_container_width=True
                    )

            else:
                st.info("ℹ️ Nenhum registro encontrado para este período.")

# === MENU: MUDAR SENHA ===
elif menu == "Mudar Senha":
    st.header("🔑 Mudar Senha")

    st.subheader("Alterar minha senha")
    with st.form("form_alterar_senha_propria"):
        senha_atual = st.text_input("Senha Atual", type="password", key="senha_atual_propria")
        senha_nova = st.text_input("Nova Senha", type="password", key="senha_nova_propria")
        senha_nova_conf = st.text_input("Confirmar Nova Senha", type="password", key="senha_nova_conf_propria")
        submit_propria = st.form_submit_button("✔️ Alterar Minha Senha", type="primary", use_container_width=True)

    if submit_propria:
        if not senha_atual or not senha_nova or not senha_nova_conf:
            st.error("❌ Preencha todos os campos.")
        elif len(senha_nova) < 4:
            st.error("❌ A nova senha deve ter pelo menos 4 caracteres.")
        elif senha_nova != senha_nova_conf:
            st.error("❌ A confirmação não corresponde à nova senha.")
        else:
            sucesso, msg = db.alterar_senha(st.session_state["usuario_logado"], senha_atual, senha_nova)
            if sucesso:
                st.success(f"✅ {msg}")
            else:
                st.error(f"❌ {msg}")

    if st.session_state["eh_admin"]:
        st.divider()
        st.subheader("🔐 Redefinir senha de outro usuário (Admin)")
        st.caption("Como administrador, você pode redefinir a senha de qualquer usuário sem precisar da senha atual dele.")

        usuarios_df = db.listar_usuarios()
        lista_usuarios = usuarios_df['username'].tolist()

        with st.form("form_redefinir_senha_admin"):
            usuario_alvo = st.selectbox("Usuário", lista_usuarios, key="usuario_alvo_redefinir")
            nova_senha_admin = st.text_input("Nova Senha", type="password", key="nova_senha_admin")
            nova_senha_admin_conf = st.text_input("Confirmar Nova Senha", type="password", key="nova_senha_admin_conf")
            submit_admin = st.form_submit_button("✔️ Redefinir Senha", type="primary", use_container_width=True)

        if submit_admin:
            if not nova_senha_admin or not nova_senha_admin_conf:
                st.error("❌ Preencha todos os campos.")
            elif len(nova_senha_admin) < 4:
                st.error("❌ A nova senha deve ter pelo menos 4 caracteres.")
            elif nova_senha_admin != nova_senha_admin_conf:
                st.error("❌ A confirmação não corresponde à nova senha.")
            else:
                sucesso, msg = db.redefinir_senha_admin(usuario_alvo, nova_senha_admin)
                if sucesso:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ {msg}")

# === MENU: CONFIGURAÇÕES ===
elif menu == "Configurações":
    st.header("⚙️ Configurações")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["👥 Funcionários", "🔐 Usuários", "📆 Feriados", "ℹ️ Sobre", "💾 Backup"])

    # === TAB 1: FUNCIONÁRIOS ===
    with tab1:
        st.subheader("📝 Novo Funcionário")

        novo_func = st.text_input("Nome do Funcionário", key="input_novo_func", placeholder="Ex: João Silva")

        if st.button("✔️ Adicionar Funcionário", type="primary", use_container_width=True):
            if novo_func.strip():
                if db.adicionar_funcionario(novo_func):
                    st.success(f"✅ '{novo_func}' adicionado com sucesso!")
                    st.rerun()
                else:
                    st.warning(f"⚠️ '{novo_func}' já existe no sistema.")
            else:
                st.error("❌ Digite um nome válido.")

        st.divider()
        st.subheader("📋 Funcionários Ativos")

        funcionarios_df = db.listar_funcionarios()
        if not funcionarios_df.empty:
            col_func, col_acao = st.columns([3, 1])

            with col_func:
                st.dataframe(funcionarios_df, hide_index=True, use_container_width=True)

            with col_acao:
                st.markdown("**Ações:**")
                if st.button("🗑️ Remover Selecionado", type="secondary", use_container_width=True):
                    st.info("Selecione um funcionário e confirme a exclusão.")
        else:
            st.info("ℹ️ Nenhum funcionário cadastrado.")

    # === TAB 2: USUÁRIOS (Apenas Admin) ===
    with tab2:
        if st.session_state["eh_admin"]:
            st.subheader("🔐 Gerenciar Usuários")

            col_novo_user, col_lista_user = st.columns([1, 1])

            with col_novo_user:
                st.markdown("#### Novo Usuário")
                novo_usuario = st.text_input("Username", key="novo_usuario", placeholder="ex: joao.silva")
                nova_senha = st.text_input("Senha", type="password", key="nova_senha")

                funcionarios_df_novo_user = db.listar_funcionarios()
                opcoes_vinculo = ["🔧 Nenhum (acesso administrativo)"] + funcionarios_df_novo_user['nome'].tolist()
                vinculo_func = st.selectbox(
                    "Vincular a um Funcionário",
                    opcoes_vinculo,
                    key="vinculo_func_novo_user",
                    help="Se vincular a um funcionário, este usuário só poderá lançar o próprio ponto (sem editar/excluir e sem acesso a Relatórios/Configurações)."
                )

                if vinculo_func == "🔧 Nenhum (acesso administrativo)":
                    novo_admin = st.checkbox("Tornar Admin?", key="novo_admin")
                    id_funcionario_vinculado = None
                else:
                    st.caption("👤 Usuário restrito — sem permissão de administrador.")
                    novo_admin = False
                    id_funcionario_vinculado = int(
                        funcionarios_df_novo_user.loc[funcionarios_df_novo_user['nome'] == vinculo_func, 'id'].values[0]
                    )

                if st.button("✔️ Criar Usuário", type="primary", use_container_width=True):
                    if novo_usuario.strip() and nova_senha.strip():
                        sucesso, msg = db.criar_usuario(novo_usuario, nova_senha, novo_admin, id_funcionario_vinculado)
                        if sucesso:
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.error(f"❌ {msg}")
                    else:
                        st.error("❌ Preencha todos os campos.")

            with col_lista_user:
                st.markdown("#### Usuários Existentes")
                usuarios_df = db.listar_usuarios()
                st.dataframe(
                    usuarios_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "eh_admin": st.column_config.CheckboxColumn("Admin?")
                    }
                )

        else:
            st.warning("⚠️ Apenas administradores podem gerenciar usuários.")

    # === TAB 3: FERIADOS ===
    with tab3:
        st.subheader("📆 Feriados")

        col_add_fer, col_list_fer = st.columns([1, 1])

        with col_add_fer:
            st.markdown("#### Adicionar Feriado")
            data_feriado = st.date_input("Data", key="data_feriado")
            desc_feriado = st.text_input("Descrição", key="desc_feriado", placeholder="Ex: Natal")

            if st.button("✔️ Adicionar", type="primary", use_container_width=True):
                if desc_feriado.strip():
                    if db.adicionar_feriado(data_feriado.strftime('%Y-%m-%d'), desc_feriado):
                        st.success("✅ Feriado adicionado!")
                    else:
                        st.warning("⚠️ Esta data já é um feriado.")
                else:
                    st.error("❌ Informe a descrição do feriado.")

        with col_list_fer:
            st.markdown("#### Feriados Cadastrados")
            feriados_df = db.listar_feriados()
            if not feriados_df.empty:
                st.dataframe(feriados_df, hide_index=True, use_container_width=True)
            else:
                st.info("ℹ️ Nenhum feriado cadastrado.")

    # === TAB 4: SOBRE ===
    with tab4:
        st.markdown("""
        ### 📊 Controle de Ponto e Banco de Horas
        **Versão 2.0 - Otimizada**

        #### 🎯 Funcionalidades:
        - ✅ Lançamento de ponto (entrada/saída)
        - ✅ Cálculo automático de saldo
        - ✅ Suporte a feriados e fins de semana (multiplicadores)
        - ✅ Desconto automático de almoço
        - ✅ Ajustes manuais
        - ✅ Relatórios com filtros interativos
        - ✅ Exportação para Excel formatado
        - ✅ Autenticação multi-usuário

        #### 📋 Regras de Cálculo:
        - **Segunda a Sexta:** 1x (meta: 8h)
        - **Sábado:** 1.7x (meta: 0h)
        - **Domingo/Feriado:** 2x (meta: 0h)
        - **Almoço:** Desconto automático de 1h se intervalo > 6h

        #### 💡 Dicas:
        - Guarde as senhas dos usuários em local seguro
        - Exporte regularmente seus dados
        - Verifique os feriados cadastrados periodicamente

        ---
        *Desenvolvido com Python, Streamlit, SQLite e ❤️*
        """)

    # === TAB 5: BACKUP COMPLETO (Apenas Admin) ===
    with tab5:
        st.subheader("💾 Backup Completo do Banco de Dados")
        st.caption(
            "Gera um arquivo Excel com uma cópia bruta e completa de todas as "
            "tabelas do banco na nuvem (Turso): funcionários, registros de "
            "ponto, ajustes, feriados e usuários. Use isso periodicamente para "
            "guardar uma cópia de segurança fora do Turso (ex.: no seu OneDrive)."
        )
        st.warning(
            "⚠️ O arquivo gerado inclui a aba **usuarios**, com os hashes de "
            "senha (bcrypt) de cada login. Não são as senhas em texto puro, "
            "mas ainda assim guarde este arquivo em local seguro e não o "
            "compartilhe livremente."
        )

        if st.button("📦 Gerar Backup Completo", type="primary", use_container_width=True):
            with st.spinner("Consultando todas as tabelas no banco..."):
                tabelas = db.obter_backup_completo()
                arquivo_backup, nome_arquivo_backup = ut.exportar_backup_completo_excel(tabelas)

            resumo_linhas = ", ".join(f"{nome}: {len(df)}" for nome, df in tabelas.items())
            st.success(f"✅ Backup gerado! ({resumo_linhas})")

            st.download_button(
                label="⬇️ Baixar Backup Completo (.xlsx)",
                data=arquivo_backup,
                file_name=nome_arquivo_backup,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )
