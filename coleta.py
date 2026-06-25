# ----- Importações -----
import requests
import sqlite3
import time
import json
from collections import deque

# ----- Configurações -----
API_KEY = "RGAPI-af311729-96ab-44db-9423-1e657c04049a"
ROTA_AMERICAS = "https://americas.api.riotgames.com"
HEADERS = {'X-Riot-Token': API_KEY}
LIMITE_PARTIDAS = 10000
PATCH_START_TIMESTAMP = 1777431600
DURACAO_MINIMA_SEGUNDOS = 300

# ----- Banco de Dados -----
def inicializar_banco():
    conn = sqlite3.connect('lol_analytics.db')
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS matches (match_id TEXT PRIMARY KEY, version TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS stats (match_id TEXT, champion TEXT, win INTEGER)')
    cursor.execute('CREATE TABLE IF NOT EXISTS raw_matches (match_id TEXT PRIMARY KEY, json_data TEXT)')
    conn.commit()
    return conn

def pegar_ultimo_match_do_banco():
    conn = sqlite3.connect('lol_analytics.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT count(name) FROM sqlite_master WHERE type='table' AND name='matches'")
    if cursor.fetchone()[0] == 1:
        cursor.execute("SELECT match_id FROM matches ORDER BY ROWID DESC LIMIT 1")
        resultado = cursor.fetchone()
        conn.close()
        if resultado:
            return resultado[0]

    conn.close()
    return None

# ----- Requisições -----
def obter_puuid(game_name, tag_line):
    print(f"Buscando PUUID para {game_name}#{tag_line}...")
    url_account = f"{ROTA_AMERICAS}/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"

    resp = requests.get(url_account, headers=HEADERS)
    if resp.status_code == 200:
        puuid = resp.json()['puuid']
        print(f"PUUID encontrado: {puuid[:8]}...")
        return puuid
    else:
        print(f"Erro ao buscar jogador! Status Code: {resp.status_code}")
        return None

def extrair_puuid_da_partida(match_id):
    print(f"Retomando progresso... Extraindo PUUID da partida {match_id}")
    url_detail = f"{ROTA_AMERICAS}/lol/match/v5/matches/{match_id}"
    resp = requests.get(url_detail, headers=HEADERS)

    if resp.status_code == 200:
        return resp.json()['info']['participants'][0]['puuid']

    print(f"Erro ao acessar partida salva. Status: {resp.status_code}")
    return None

# ----- Execução Snowball -----
def executar_snowball(puuid_inicial):
    conn = inicializar_banco()
    cursor = conn.cursor()

    fila_puuids = deque([puuid_inicial])
    puuids_visitados = set([puuid_inicial])
    partidas_processadas = set()

    cursor.execute("SELECT match_id FROM matches")
    for row in cursor.fetchall():
        partidas_processadas.add(row[0])

    print("Iniciando coleta Snowball...")

    while fila_puuids and len(partidas_processadas) < LIMITE_PARTIDAS:
        puuid_atual = fila_puuids.popleft()
        print(f"\n[+] Buscando partidas do jogador: {puuid_atual[:8]}...")

        url_matches = f"{ROTA_AMERICAS}/lol/match/v5/matches/by-puuid/{puuid_atual}/ids?queue=420&startTime={PATCH_START_TIMESTAMP}&start=0&count=20"
        resp_matches = requests.get(url_matches, headers=HEADERS)
        time.sleep(1.5)

        if resp_matches.status_code == 429:
            print("Rate limit atingido na busca de IDs. Esperando 10s...")
            time.sleep(10)
            fila_puuids.appendleft(puuid_atual) 
            continue

        if resp_matches.status_code != 200:
            print("resp_matches.status_code != 200")
            continue 

        match_ids = resp_matches.json()

        for match_id in match_ids:
            if match_id in partidas_processadas or len(partidas_processadas) >= LIMITE_PARTIDAS:
                continue

            print(f"    Baixando partida: {match_id}")
            url_detail = f"{ROTA_AMERICAS}/lol/match/v5/matches/{match_id}"
            resp_detail = requests.get(url_detail, headers=HEADERS)

            time.sleep(1.5)

            if resp_detail.status_code == 429:
                print("    Rate limit atingido no detalhe da partida! Esperando...")
                time.sleep(10)
                continue

            if resp_detail.status_code != 200:
                continue

            match_data = resp_detail.json()

            game_duration = match_data['info']['gameDuration']
            if game_duration < DURACAO_MINIMA_SEGUNDOS:
                print(f"    Partida ignorada (Remake / Duração: {game_duration}s)")
                partidas_processadas.add(match_id)
                continue  

            json_string = json.dumps(match_data)
            cursor.execute('INSERT OR IGNORE INTO raw_matches VALUES (?, ?)', (match_id, json_string))

            full_version = match_data['info']['gameVersion']
            patch = ".".join(full_version.split('.')[:2])
            cursor.execute('INSERT OR IGNORE INTO matches VALUES (?, ?)', (match_id, patch))

            for p in match_data['info']['participants']:
                novo_puuid = p['puuid']

                if novo_puuid not in puuids_visitados:
                    puuids_visitados.add(novo_puuid)
                    fila_puuids.append(novo_puuid)

                cursor.execute('INSERT INTO stats VALUES (?, ?, ?)',
                               (match_id, p['championName'], 1 if p['win'] else 0))

            partidas_processadas.add(match_id)
            conn.commit()
            print(f"    -> Salvo! Total no banco: {len(partidas_processadas)}")

    conn.close()
    print("\nColeta finalizada ou limite atingido!")

# ----- Ponto de Entrada -----
if __name__ == "__main__":
    NOME_JOGADOR = "frz"
    TAG_JOGADOR = "FLA"

    puuid_semente = None

    ultimo_match = pegar_ultimo_match_do_banco()

    if ultimo_match:
        puuid_semente = extrair_puuid_da_partida(ultimo_match)
        time.sleep(1.5)  

    if not puuid_semente:
        print("Iniciando com semente primária...")
        puuid_semente = obter_puuid(NOME_JOGADOR, TAG_JOGADOR)

    if puuid_semente:
        executar_snowball(puuid_semente)