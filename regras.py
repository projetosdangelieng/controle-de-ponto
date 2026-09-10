from datetime import datetime

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
