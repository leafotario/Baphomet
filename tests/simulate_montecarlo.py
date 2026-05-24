import sys
import os
import math

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from occult_ui_framework import AbyssalRNG

def simulate_game(name, sim_func, iterations=1000000, bet=100):
    rng = AbyssalRNG()
    total_bet = iterations * bet
    total_payout = 0
    payouts = []
    
    print(f"[{name}] Iniciando simulação de {iterations} rodadas (Aposta base: {bet})...")
    
    for _ in range(iterations):
        payout = sim_func(rng, bet)
        total_payout += payout
        payouts.append(payout)
        
    house_profit = total_bet - total_payout
    house_edge = (house_profit / total_bet) * 100.0
    
    # Calculate Standard Deviation of payout
    mean_payout = total_payout / iterations
    variance = sum((p - mean_payout) ** 2 for p in payouts) / iterations
    std_dev = math.sqrt(variance)
    
    print(f"[{name}] Resultados:")
    print(f"  Total Apostado: {total_bet}")
    print(f"  Total Pago: {total_payout}")
    print(f"  Lucro da Casa: {house_profit}")
    print(f"  House Edge: {house_edge:.4f}%")
    print(f"  Desvio Padrão do Retorno: {std_dev:.4f}")
    if house_edge < 1.0:
        print(f"  [ALERTA] Matemática comprometida! House Edge menor que 1%.\n")
    else:
        print(f"  [OK] Margem segura matematicamente sustentável.\n")

def sim_macabra(rng, bet):
    # Roda Macabra: 0 a 5.
    # 0 = perde
    # 1, 2 = ganha 2.0x (base) com house edge aplicado
    # 3, 4, 5 = ganha 0
    choice = rng.generate_int(0, 5)
    if choice in (1, 2):
        adjusted = rng.calculate_house_edge(1.0, 2.0) # house_edge default 0.02 -> 2.0 * 0.98 = 1.96
        return int(bet * adjusted)
    return 0

def sim_ossos(rng, bet):
    # Craps
    # 7, 11 = vence primeira rodada -> raw 2.0
    # 2, 3, 12 = perde
    # Outros = point. Role ate dar point (vence 2.0) ou 7 (perde)
    d1 = rng.generate_int(1, 6)
    d2 = rng.generate_int(1, 6)
    total = d1 + d2
    
    if total in (7, 11):
        adjusted = rng.calculate_house_edge(0.5, 2.0) # -> 1.96
        return int(bet * adjusted)
    elif total in (2, 3, 12):
        return 0
    else:
        point = total
        while True:
            d1 = rng.generate_int(1, 6)
            d2 = rng.generate_int(1, 6)
            new_t = d1 + d2
            if new_t == point:
                adjusted = rng.calculate_house_edge(0.5, 2.0)
                return int(bet * adjusted)
            elif new_t == 7:
                return 0

def sim_leviata(rng, bet):
    # Leviata: 1 em 10.000 de chance de levar o "jackpot".
    # Na simulacao, jackpot nao existe, mas o payout cruze e' bet * 10
    # Alem disso, 10% vai pro jackpot nas perdas.
    # O EV cru sem jackpot real: 9999 perdas (pagando 0) e 1 vitoria (pagando bet*10 + jackpot acumulado).
    # Aqui vamos simular so a prob. e payout estatico.
    # Para ser exato com o código: ganha bet*10. Mas se não tiver jackpot a edge seria de 99.9%?
    roll = rng.generate_int(1, 10000)
    if roll == 6666:
        # Payout base 10x 
        return int(bet * 10)
    return 0

if __name__ == "__main__":
    simulate_game("Roda do Tormento (Macabra)", sim_macabra, 1000000, 100)
    simulate_game("Ossos dos Condenados", sim_ossos, 1000000, 100)
    simulate_game("Leviatã (Sacrifício Supremo)", sim_leviata, 1000000, 100)
