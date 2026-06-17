import requests
import pandas as pd
import json
import os
from io import StringIO
from datetime import date, timedelta
from collections import defaultdict

CLIENT_ID     = os.environ["NTZ_CLIENT_ID"]
CLIENT_SECRET = os.environ["NTZ_CLIENT_SECRET"]

# ---------- 1. Token ----------
def get_token():
    r = requests.post(
        "https://identity.netztransparenz.de/users/connect/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID.strip(),
            "client_secret": CLIENT_SECRET.strip(),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    r.raise_for_status()
    return r.json()["access_token"]

# ---------- 2. Veri cek ----------
def fetch_nrv(date_from, date_to, token):
    r = requests.get(
        f"https://ds.netztransparenz.de/api/v1/data/NrvSaldo/NRVSaldo/Betrieblich/{date_from}/{date_to}",
        headers={"Authorization": f"Bearer {token}"}
    )
    r.raise_for_status()
    return pd.read_csv(StringIO(r.text), sep=";", decimal=",")

# ---------- 3. CSV guncelle ----------
def update_csv(new_df):
    path = "data/nrv_saldo.csv"
    os.makedirs("data", exist_ok=True)
    if os.path.exists(path):
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_df]).drop_duplicates(
            subset=["Datum","von","bis"]
        )
    else:
        combined = new_df
    combined.to_csv(path, index=False)
    print(f"CSV: {len(combined)} satir")
    return combined

# ---------- 4. JSON uret (Almanya saatine cevrilmis) ----------
def build_json(df):
    os.makedirs("data", exist_ok=True)
    df = df.copy()

    # UTC zaman damgasi olustur: Datum (DD.MM.YYYY) + von (HH:MM)
    dt_str = df["Datum"] + " " + df["von"]
    ts_utc = pd.to_datetime(dt_str, format="%d.%m.%Y %H:%M", utc=True)

    # Almanya saatine cevir (yaz/kis otomatik)
    ts_local = ts_utc.dt.tz_convert("Europe/Berlin")

    df["Deutschland"] = pd.to_numeric(df["Deutschland"], errors="coerce")
    df["local_dt"]    = ts_local
    df["date_str"]    = ts_local.dt.strftime("%Y-%m-%d")
    df["hour"]        = ts_local.dt.hour
    df["minute"]      = ts_local.dt.minute

    def quarter(minute):
        return {0:"Q1", 15:"Q2", 30:"Q3", 45:"Q4"}.get(minute, "Q1")

    df["quarter"] = df["minute"].apply(quarter)
    df["key"]     = df["hour"].apply(lambda h: f"{h:02d}") + "-" + df["quarter"]

    # date_str -> { "HH-Qx": value }
    result = defaultdict(dict)
    for _, row in df.iterrows():
        val = row["Deutschland"]
        if pd.notna(val):
            result[row["date_str"]][row["key"]] = round(float(val), 1)

    result = dict(sorted(result.items()))
    with open("data/nrv_data.json", "w") as f:
        json.dump(result, f)
    print(f"JSON: {len(result)} gun (Almanya saati)")
    return result

# ---------- 5. HTML uret ----------
def build_html(result):
    dates = sorted(result.keys())
    first = dates[0] if dates else ""
    last  = dates[-1] if dates else ""

    template_path = os.path.join(os.path.dirname(__file__), "template.html")
    html = open(template_path).read()
    html = html.replace("__DATA_JSON__", json.dumps(result))
    html = html.replace("__FIRST_DATE__", first)
    html = html.replace("__LAST_DATE__", last)
    html = html.replace("__UPDATED_AT__", date.today().strftime("%Y-%m-%d"))

    with open("index.html", "w") as f:
        f.write(html)
    print(f"HTML olusturuldu: {first} -> {last}")

# ---------- MAIN ----------
if __name__ == "__main__":
    token = get_token()
    print("Token OK")

    # Bu ayin basindan bugune (1 gun fazla cek, UTC->local kaymasi icin)
    today     = date.today()
    date_from = today.replace(day=1).strftime("%Y-%m-%d")
    date_to   = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    new_df = fetch_nrv(date_from, date_to, token)
    print(f"Cekilen: {len(new_df)} satir")

    df = update_csv(new_df)
    result = build_json(df)
    build_html(result)
    print("Tamamlandi.")
