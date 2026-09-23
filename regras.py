from datetime import datetime, timedelta, timezone

# Fuso horário de Brasília. O servidor (Streamlit Cloud) roda em UTC, então
# 'date.today()' / 'datetime.now()' sem fuso NÃO refletem o horário de Brasília.
# Se o banco de fusos do sistema não estiver disponível, usa UTC-3 fixo
# (o Brasil não tem horário de verão desde 2019).
try:
    from zoneinfo import ZoneInfo
    FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")
except Exception:
    FUSO_BRASILIA = timezone(timedelta(hours=-3))


def agora_brasilia():
    """Data e hora atuais em Brasília."""
    return datetime.now(FUSO_BRASILIA)


def hoje_brasilia():
    """Data de hoje em Brasília (use no lugar de date.today())."""
    return agora_brasilia().date()


def validar_horario_nao_futuro(data_str, *horarios):
    """
    Impede lançamentos com data ou horário no futuro, pelo relógio de Brasília.
    - data_str: 'AAAA-MM-DD'
    - horarios: um ou mais 'HH:MM' (vazios são ignorados)
    Retorna (ok, mensagem). Se ok=False, a mensagem explica o motivo.
    """
    agora = agora_brasilia()
    hoje = agora.date()
    data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()

    if data_obj > hoje:
        return False, (
            f"Não é permitido lançar em data futura. "
            f"Hoje em Brasília é {hoje.strftime('%d/%m/%Y')}."
        )

    if data_obj == hoje:
        hora_atual = agora.strftime('%H:%M')
        for h in horarios:
            if h and h > hora_atual:
                return False, (
                    f"Não é permitido lançar horário futuro. "
                    f"Agora em Brasília são {hora_atual} e você informou {h}."
                )

    return True, ""


def validar_entrada_horario_atual(data_str, hora_str, tolerancia_min=10):
    """
    Regra da ENTRADA para usuários comuns: só vale o dia de hoje e um horário
    entre (agora - tolerancia_min) e agora, pelo relógio de Brasília.
    Não aceita horário anterior a essa janela nem horário futuro.
    Retorna (ok, mensagem).
    """
    agora = agora_brasilia()
    hoje = agora.date()
    data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()

    if data_obj != hoje:
        return False, (
            f"A entrada só pode ser registrada no dia de hoje "
            f"({hoje.strftime('%d/%m/%Y')}). Retroativos são feitos pelo administrador."
        )

    hh, mm = map(int, hora_str.split(':'))
    minutos_informado = hh * 60 + mm
    minutos_agora = agora.hour * 60 + agora.minute
    hora_atual = agora.strftime('%H:%M')

    if minutos_informado > minutos_agora:
        return False, (
            f"Não é permitido lançar horário futuro. "
            f"Agora em Brasília são {hora_atual} e você informou {hora_str}."
        )

    if minutos_informado < minutos_agora - tolerancia_min:
        return False, (
            f"Horário anterior ao permitido. Agora em Brasília são {hora_atual}; "
            f"a entrada aceita de {tolerancia_min} min atrás até agora. "
            f"Retroativos são feitos pelo administrador."
        )

    return True, ""


def dia_bloqueado_para_usuario(data_str, is_feriado=False):
    """
    Sábados, domingos e feriados só podem ser lançados por administradores.
    Retorna (bloqueado, motivo).
    """
    data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()
    dia = data_obj.weekday()
    if dia == 5:
        return True, "Sábado."
    if dia == 6:
        return True, "Domingo."
    if is_feriado:
        return True, "Feriado."
    return False, ""


def calcular_saldo(entrada_str, saida_str, data_str, is_feriado=False, ignorar_almoco=False):
    """
    Calcula o saldo de horas decimais do dia baseado nas regras de negócio estabelecidas.
    """
    fmt_hora = '%H:%M'
    t_entrada = datetime.strptime(entrada_str, fmt_hora)
    t_saida = datetime.strptime(saida_str, fmt_hora)
    
    diff_minutos = (t_saida - t_entrada).total_seconds() / 60.0
    
    # Regra do Almoço: Desconta 1h se intervalo > 6h (360 min) e não houve exceção
    descontou_almoco = False
    if not ignorar_almoco and diff_minutos > 360:
        diff_minutos -= 60
        descontou_almoco = True
        
    horas_trabalhadas = diff_minutos / 60.0
    
    # Identificar dia da semana (0=Segunda ... 5=Sábado, 6=Domingo)
    data_obj = datetime.strptime(data_str, '%Y-%m-%d')
    dia_semana = data_obj.weekday()
    
    # Multiplicadores e Meta Diária
    if dia_semana == 6 or is_feriado:
        horas_computadas = horas_trabalhadas * 2.0  # +100%
        meta = 0.0
    elif dia_semana == 5:
        horas_computadas = horas_trabalhadas * 1.7  # +70%
        meta = 0.0
    else:
        horas_computadas = horas_trabalhadas
        meta = 8.0 if not is_feriado else 0.0
        
    saldo_dia = horas_computadas - meta
    
    return round(saldo_dia, 2), descontou_almoco
